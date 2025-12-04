import streamlit as st
import cloudscraper
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io
import requests # Needed to fetch image bytes for Gemini
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

# --- 1. CONFIGURATION & ASSETS ---

st.set_page_config(page_title="ZenArb", page_icon="⚡", layout="wide")

# Custom SVG Logo
ZEN_LOGO_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 50" width="300" height="50">
  <g transform="translate(0, 0) scale(0.12)">
    <path d="
      M 85 100 
      Q 200 170 315 100 
      L 340 135 
      Q 270 200 340 265 
      L 315 300 
      Q 200 230 85 300 
      L 60 265 
      Q 130 200 60 135 
      Z" 
      fill="#E0E0E0" 
      stroke="none"
    />
  </g>
  <text x="50" y="32" font-family="Verdana, Geneva, sans-serif" font-size="24" font-weight="bold" fill="#E0E0E0" letter-spacing="1px">ZenArb</text>
</svg>
"""

# Fixed Exchange Rate (EUR to JPY) - Needs manual update periodically
DEFAULT_EUR_TO_JPY_RATE = 181.0  

DEFAULT_NEGATIVE_KEYWORDS = [
    "link", "komas", "belt", "strap", "buckle", "clasp", "bezel", 
    "glass", "crystal", "dial", "hands", "box", "manual", "parts", 
    "修理", "部品", "駒", "ベルト", "ガラス", "風防", "文字盤", "針", "箱", "説明書", "ジャンク", 
    "women's", "ladies", "lady's", "女性", "婦人", "ガール", "レディース" 
]

PLATFORM_ENDPOINTS = {
    "Yahoo Auctions": "yahoo.aspx",
    "Mercari": "mercari.aspx",
    "Rakuten Rakuma": "rakuma.aspx",
    "Yahoo Shopping": "yshopping.aspx",
}

REQUIRED_COLUMNS = [
    "Platform", "Target Model", "Min EUR Floor (€)", "Min JPY Floor (Internal)",
    "Qualified", "Status/Reason", "Title", "Price JPY", "Price EUR (€)", "Image URL", "ZenMarket Link",
    "Source Query", "AI Verdict"
]

# Mapping for Sort Options to ZenMarket URL Parameters
SORT_STRATEGIES = {
    "Ending Soonest": {"sort": "end", "order": "asc"},
    "Newly Listed": {"sort": "new", "order": "desc"},
    "Price: Low to High": {"sort": "price", "order": "asc"},
    "Price: High to Low": {"sort": "price", "order": "desc"}
}

# --- Helper function to initialize default targets ---
def get_default_targets() -> pd.DataFrame:
    """Provides a default DataFrame structure for the user to edit."""
    data = {
        "Model Name": ["Omega De Ville (Mercari)", "Rolex Datejust 1601"],
        "Search Query": ["Omega De Ville", "Rolex 1601"],
        "Min EUR Floor (€)": [170, 2500],
        "Max EUR Ceiling (€)": [500, 3500]
    }
    return pd.DataFrame(data)

# Initialize session state 
if 'target_df' not in st.session_state:
    st.session_state['target_df'] = get_default_targets()
if 'results_df' not in st.session_state:
    st.session_state['results_df'] = pd.DataFrame()
if 'eur_to_jpy' not in st.session_state:
    st.session_state['eur_to_jpy'] = DEFAULT_EUR_TO_JPY_RATE
if 'neg_keywords_str' not in st.session_state:
    st.session_state['neg_keywords_str'] = "\n".join(DEFAULT_NEGATIVE_KEYWORDS)
if 'sort_strategy' not in st.session_state:
    st.session_state['sort_strategy'] = "Ending Soonest"
if 'request_delay' not in st.session_state:
    st.session_state['request_delay'] = (1.5, 3.0)
if 'selected_platforms' not in st.session_state:
    st.session_state['selected_platforms'] = list(PLATFORM_ENDPOINTS.keys())
if 'google_api_key' not in st.session_state:
    st.session_state['google_api_key'] = ""
if 'enable_ai' not in st.session_state:
    st.session_state['enable_ai'] = False


# --- 2. CORE SCRAPING AND FILTERING LOGIC ---

def is_qualified(title: str, price_jpy: float, min_jpy_floor: int, max_jpy_ceiling: int, negative_keywords: list) -> tuple[bool, str]:
    """Applies textual and price floor/ceiling filters using JPY value."""
    
    if price_jpy < min_jpy_floor:
        return False, f"Price too low (¥{int(price_jpy):,})"
        
    if max_jpy_ceiling > 0 and price_jpy > max_jpy_ceiling:
        return False, f"Price too high (¥{int(price_jpy):,})"

    title_lower = title.lower()
    for word in negative_keywords:
        if word.strip() and word.strip().lower() in title_lower:
            return False, f"Detected keyword: '{word}'"

    return True, "Qualified by Text"

def get_ai_verdict(image_url: str, target_model: str, api_key: str) -> str:
    """Uses Google Gemini 1.5 Flash to verify if the image matches the target model."""
    if not api_key or not genai:
        return "AI Skipped (No Key/Lib)"
    
    # Skip placeholders
    if "placehold.co" in image_url:
        return "No Image"

    try:
        # Fetch image bytes first (Gemini prefers inline data or uploaded files)
        img_response = requests.get(image_url, timeout=10)
        if img_response.status_code != 200:
            return "Image Load Fail"
        
        image_bytes = img_response.content
        
        client = genai.Client(api_key=api_key)
        
        prompt = f"Is this image a {target_model}? Respond with 'YES' if it is clearly the correct watch model. Respond with 'NO' if it is a different watch, a box/accessory only, or completely unrelated. Respond with 'UNCERTAIN' if the image is too blurry or ambiguous."

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg" # Assuming JPEG, Gemini is forgiving
                ),
                prompt
            ]
        )
        
        return response.text.strip()
        
    except Exception as e:
        return f"AI Error: {str(e)[:20]}"

def run_platform_scrape(platform_name: str, endpoint: str, query: str, min_eur_floor: float, max_eur_ceiling: float, eur_to_jpy_rate: float, negative_keywords: list, max_pages: int, sort_params: dict, delay_range: tuple, enable_ai: bool, api_key: str) -> pd.DataFrame:
    """Fetches data from a specific ZenMarket platform with pagination."""
    
    min_jpy_floor = min_eur_floor * eur_to_jpy_rate
    max_jpy_ceiling = max_eur_ceiling * eur_to_jpy_rate
    
    scraper = cloudscraper.create_scraper()
    base_url = f"https://zenmarket.jp/en/{endpoint}"
    
    all_results = []
    
    for page in range(1, max_pages + 1):
        # Apply dynamic sort parameters here
        params = {'q': query, 'p': page}
        params.update(sort_params) # Merge sort/order params
        
        try:
            # Use dynamic delay range
            time.sleep(random.uniform(delay_range[0], delay_range[1]))
            
            response = scraper.get(base_url, params=params)
            if response.status_code != 200: break
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # --- PLATFORM-SPECIFIC SELECTOR CHAIN ---
            items = []
            
            # Use the general product container wrapper identified in the Mercari HTML:
            product_container = soup.select_one('#productsContainer') 
            
            if product_container:
                items = product_container.select('.product')
            
            if not items:
                items = soup.select('#yahoo-search-results .yahoo-search-result')

            if not items: break

            page_results_count = 0
            
            for item in items:
                title_tag = item.select_one('.item-title') or \
                            item.select_one('.translate a') or \
                            item.select_one('h3')
                            
                if not title_tag: continue
                title = title_tag.text.strip()
                
                link_tag = item.select_one('a.product-item') or item.select_one('a')
                link_href = link_tag['href'] if link_tag and 'href' in link_tag.attrs else ''
                
                if not link_href: continue
                link = "https://zenmarket.jp/en/" + link_href if not link_href.startswith('http') else link_href
                
                img_tag = item.select_one('.img-wrap img')
                img_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else "https://placehold.co/100x100/CCCCCC/000000?text=No+Image"

                price_tag = item.select_one('.price .amount') or item.select_one('.auction-price .amount')
                price_jpy = 0
                price_eur = 0.0
                
                if price_tag and 'data-jpy' in price_tag.attrs:
                    try:
                        price_jpy = float(price_tag['data-jpy'].replace('¥','').replace(',',''))
                        price_eur = round(price_jpy / eur_to_jpy_rate, 2)
                    except:
                        pass
                
                is_valid, status_reason = is_qualified(title, price_jpy, min_jpy_floor, max_jpy_ceiling, negative_keywords)
                
                ai_verdict = "N/A"
                # Run AI Vision only on Qualified items to save cost
                if enable_ai and is_valid:
                    ai_verdict = get_ai_verdict(img_url, query, api_key)
                    # Simple sleep to respect rate limits if looping AI fast
                    time.sleep(0.5)
                
                all_results.append({
                    "Platform": platform_name,
                    "Target Model": query,
                    "Min EUR Floor (€)": min_eur_floor,
                    "Max EUR Ceiling (€)": max_eur_ceiling,
                    "Min JPY Floor (Internal)": int(min_jpy_floor),
                    "Qualified": is_valid,
                    "Status/Reason": status_reason,
                    "Title": title,
                    "Price JPY": price_jpy,
                    "Price EUR (€)": price_eur, 
                    "Image URL": img_url,
                    "ZenMarket Link": link,
                    "Source Query": query,
                    "AI Verdict": ai_verdict
                })
                page_results_count += 1
            
            if page_results_count < 5: break
                
        except Exception as e:
            break
            
    return pd.DataFrame(all_results, columns=REQUIRED_COLUMNS)


# --- 3. STREAMLIT UI & EXPORT ---

# --- SIDEBAR: PRODUCT BRANDING & PROPERTIES ---
with st.sidebar:
    # 1. Logo & Description
    st.markdown(f"{ZEN_LOGO_SVG}", unsafe_allow_html=True)
    st.markdown("""
    **Automated Arbitrage Scout**
    
    *Scan JDM marketplaces for undervalued watches.*
    """)
    st.divider()

    # 2. Scanner Properties
    st.header("⚙️ Properties")
    
    # AI Vision Integration (Gemini)
    st.subheader("🤖 AI Vision (Gemini)")
    st.session_state['google_api_key'] = st.text_input("Google API Key", value=st.session_state['google_api_key'], type="password")
    st.session_state['enable_ai'] = st.checkbox("Enable Vision (Slower)", value=st.session_state['enable_ai'], help="Analyze images of qualified listings using Gemini 1.5 Flash to confirm model match.")
    
    if st.session_state['enable_ai'] and not st.session_state['google_api_key']:
        st.warning("⚠️ Google API Key required.")

    st.divider()
    
    # Scraping Depth
    scrape_depth = st.number_input(
        "Pages to Scrape", 
        min_value=1, max_value=10, value=1,
        help="Number of pages to fetch per model. Higher depth takes longer."
    )

    # Platform Selection
    st.subheader("Platforms")
    st.session_state['selected_platforms'] = st.multiselect(
        "Select Markets:",
        options=list(PLATFORM_ENDPOINTS.keys()),
        default=st.session_state['selected_platforms'],
        help="Choose which marketplaces to search."
    )

    # Sorting Strategy
    st.session_state['sort_strategy'] = st.selectbox(
        "Sort Results By",
        options=list(SORT_STRATEGIES.keys()),
        index=0,
        help="Determines the order in which listings are fetched."
    )
    
    # Request Delay Control
    st.session_state['request_delay'] = st.slider(
        "Request Delay (seconds)",
        min_value=0.5, max_value=5.0, value=(1.5, 3.0),
        help="Random delay between requests. Slower is safer against IP bans."
    )
    
    # Exchange Rate
    st.session_state['eur_to_jpy'] = st.number_input(
        "EUR/JPY Rate", 
        value=st.session_state['eur_to_jpy'], 
        min_value=100.0, max_value=250.0, step=0.1, format="%.1f"
    )
    
    with st.expander("Exclusion Keywords"):
        st.session_state['neg_keywords_str'] = st.text_area(
            "Filter out junk (one per line):",
            value=st.session_state['neg_keywords_str'],
            height=150
        )
    
    st.divider()
    
    # 3. Main Action
    if st.button("🚀 Launch Scouting", type="primary", use_container_width=True):
        # Validation: Check target list and platform selection
        if not st.session_state['target_df'].empty and \
           all(st.session_state['target_df']['Search Query'].astype(str).str.len() > 0) and \
           len(st.session_state['selected_platforms']) > 0:
             
             st.session_state['do_scrape'] = True
             st.session_state['results_df'] = pd.DataFrame()
        else:
            if len(st.session_state['selected_platforms']) == 0:
                st.error("Please select at least one platform.")
            else:
                st.error("Please define valid search queries.")
            st.session_state['do_scrape'] = False


# --- MAIN AREA: TARGETS ---
st.title("Watch Targets")
st.markdown("Define the models you want to hunt for. The system will auto-convert your EUR floor to JPY.")

# Ensure columns exist
current_target_df = st.session_state['target_df']
if 'Max EUR Ceiling (€)' not in current_target_df.columns:
    current_target_df['Max EUR Ceiling (€)'] = current_target_df['Min EUR Floor (€)'] * 3
    
target_editor_df = current_target_df.drop(columns=['Market Price EUR (€)'], errors='ignore')

edited_df = st.data_editor(
    target_editor_df,
    key="targets_editor",
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Model Name": st.column_config.TextColumn("Model Name", required=True),
        "Search Query": st.column_config.TextColumn("Search Query", required=True),
        "Min EUR Floor (€)": st.column_config.NumberColumn("Min Floor (€)", required=True),
        "Max EUR Ceiling (€)": st.column_config.NumberColumn("Max Ceiling (€)", required=True),
    }
)

if 'Market Price EUR (€)' not in edited_df.columns:
    edited_df['Market Price EUR (€)'] = 0

st.session_state['target_df'] = edited_df.copy() 


# --- SCRAPING LOGIC ---
if 'do_scrape' in st.session_state and st.session_state['do_scrape']:
    all_results = []
    current_neg_keywords = [k.strip().lower() for k in st.session_state['neg_keywords_str'].split('\n') if k.strip()]
    
    # Get selected sort params
    current_sort_params = SORT_STRATEGIES[st.session_state['sort_strategy']]
    current_delay_range = st.session_state['request_delay']
    
    # Filter platforms based on selection
    active_platforms = {k: v for k, v in PLATFORM_ENDPOINTS.items() if k in st.session_state['selected_platforms']}
    
    total_platforms = len(active_platforms) * len(st.session_state['target_df'])
    progress_bar = st.progress(0, text="Initializing scout...")
    step_count = 0
    
    for index, scout_data in st.session_state['target_df'].iterrows():
        query = scout_data.get("Search Query")
        min_eur_floor = scout_data.get("Min EUR Floor (€)")
        max_eur_ceiling = scout_data.get("Max EUR Ceiling (€)") 
        
        for platform_name, endpoint in active_platforms.items():
            progress_bar.progress(step_count / total_platforms, text=f"Scouting **{platform_name}** for **{query}**...")
            df_results = run_platform_scrape(
                platform_name, endpoint, query, min_eur_floor, max_eur_ceiling,
                st.session_state['eur_to_jpy'], current_neg_keywords, scrape_depth,
                current_sort_params, current_delay_range,
                st.session_state['enable_ai'], st.session_state['google_api_key']
            )
            if not df_results.empty:
                all_results.append(df_results)
            step_count += 1
    
    progress_bar.progress(1.0, text="Scouting Complete.")

    if all_results:
        final_df = pd.concat(all_results, ignore_index=True)
        st.session_state['results_df'] = final_df
        st.session_state['do_scrape'] = False 
    else:
        st.warning("Scouting completed. No results found.")
        st.session_state['do_scrape'] = False


# --- RESULTS DISPLAY ---
if not st.session_state['results_df'].empty:
    df_display = st.session_state['results_df']
    qualified_df = df_display[df_display['Qualified'] == True].copy()
    rejected_df = df_display[df_display['Qualified'] == False].copy()
    
    st.divider()
    st.subheader("Scouting Results")
    
    tab1, tab2 = st.tabs([f"✅ Qualified ({len(qualified_df)})", f"🗑️ Rejected ({len(rejected_df)})"])
    
    qualified_display = qualified_df.drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)']).rename(columns={'Min EUR Floor (€)': 'Min Floor (€)'})
    qualified_display['Price EUR (€)'] = qualified_display['Price EUR (€)'].apply(lambda x: f"€{x:,.2f}" if x is not None else 'N/A')
    
    qualified_column_config = {
        "Image URL": st.column_config.ImageColumn("Image", width="small"),
        "ZenMarket Link": st.column_config.LinkColumn("Link", width="medium", display_text="Open Listing")
    }

    with tab1:
        # Ensure AI Verdict is displayed if present
        display_cols = ["Platform", "Target Model", "Image URL", "Title", "Price JPY", "Price EUR (€)", "Min Floor (€)", "Max EUR Ceiling (€)", "ZenMarket Link", "AI Verdict"]
        st.dataframe(
            qualified_display,
            column_order=display_cols,
            column_config=qualified_column_config,
            hide_index=True,
            use_container_width=True
        )

    with tab2:
        rejected_display = rejected_df.drop(columns=['Qualified', 'Image URL', 'Min EUR Floor (€)']).rename(columns={'Min JPY Floor (Internal)': 'Min JPY Floor'})
        st.dataframe(
            rejected_display,
            column_order=["Platform", "Target Model", "Status/Reason", "Title", "Price JPY", "Min JPY Floor", "ZenMarket Link"],
            column_config={"ZenMarket Link": st.column_config.LinkColumn("Link", display_text="Open Listing")},
            hide_index=True,
            use_container_width=True
        )

    def to_excel(df: pd.DataFrame) -> bytes:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            q_export = df[df['Qualified'] == True].drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)'])
            q_export.to_excel(writer, sheet_name='Qualified Listings', index=False)
            r_export = df[df['Qualified'] == False].drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)', 'Image URL'])
            r_export.to_excel(writer, sheet_name='Rejected Candidates', index=False)
        return output.getvalue()

    st.download_button(
        label="Download Results (XLSX)",
        data=to_excel(df_display),
        file_name='zenscout_results.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type="secondary",
        use_container_width=True
    )

else:
    st.info("👈 Configure your search in the sidebar and click 'Launch Scouting'.")
