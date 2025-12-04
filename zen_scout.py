import streamlit as st
import cloudscraper
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io

# --- 1. CONFIGURATION & CONSTANTS ---

st.set_page_config(page_title="ZenMarket Multi-Platform Scout", page_icon="🕵️", layout="wide")

# Fixed Exchange Rate (EUR to JPY) - Needs manual update periodically
DEFAULT_EUR_TO_JPY_RATE = 181.0  

DEFAULT_NEGATIVE_KEYWORDS = [
    "link", "komas", "belt", "strap", "buckle", "clasp", "bezel", 
    "glass", "crystal", "dial", "hands", "box", "manual", "parts", 
    "修理", "部品", "駒", "ベルト", "ガラス", "風防", "文字盤", "針", "箱", "説明書", "ジャンク", 
    "women's", "ladies", "lady's", "女性", "婦人", "ガール", "レディース" 
]

PLATFORM_ENDPOINTS = {
    # RESTORING ALL PLATFORMS with the new selector logic
    "Yahoo Auctions": "yahoo.aspx",
    "Mercari": "mercari.aspx",
    "Rakuten Rakuma": "rakuma.aspx",
    "Yahoo Shopping": "yshopping.aspx",
}

# Define the set of required columns for the output DataFrame
REQUIRED_COLUMNS = [
    "Platform", "Target Model", "Min EUR Floor (€)", "Min JPY Floor (Internal)",
    "Qualified", "Status/Reason", "Title", "Price JPY", "Price EUR (€)", "Image URL", "ZenMarket Link",
    "Source Query"
]

# --- Helper function to initialize default targets ---
def get_default_targets() -> pd.DataFrame:
    """Provides a default DataFrame structure for the user to edit."""
    data = {
        "Model Name": ["Omega De Ville (Mercari)", "Rolex Datejust 1601"],
        "Search Query": ["Omega De Ville", "Rolex 1601"],
        "Min EUR Floor (€)": [170, 2500],  # Minimum price in EUR
        "Max EUR Ceiling (€)": [500, 3500] # NEW: Max price in EUR
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


# --- 2. CORE SCRAPING AND FILTERING LOGIC ---

def is_qualified(title: str, price_jpy: float, min_jpy_floor: int, max_jpy_ceiling: int, negative_keywords: list) -> tuple[bool, str]:
    """Applies textual and price floor/ceiling filters using JPY value."""
    
    # 1. Price Floor Check
    if price_jpy < min_jpy_floor:
        return False, f"Price too low (¥{int(price_jpy):,})"
        
    # 2. Price Ceiling Check (NEW)
    if max_jpy_ceiling > 0 and price_jpy > max_jpy_ceiling:
        return False, f"Price too high (¥{int(price_jpy):,})"


    # 3. Negative Keywords Check
    title_lower = title.lower()
    for word in negative_keywords:
        if word.strip() and word.strip().lower() in title_lower:
            return False, f"Detected keyword: '{word}'"

    return True, "Qualified by Text"

def run_platform_scrape(platform_name: str, endpoint: str, query: str, min_eur_floor: float, max_eur_ceiling: float, eur_to_jpy_rate: float, negative_keywords: list) -> pd.DataFrame:
    """Fetches data from a specific ZenMarket platform."""
    
    # CONVERT EUR FLOORS/CEILINGS TO JPY
    min_jpy_floor = min_eur_floor * eur_to_jpy_rate
    max_jpy_ceiling = max_eur_ceiling * eur_to_jpy_rate
    
    scraper = cloudscraper.create_scraper()
    url = f"https://zenmarket.jp/en/{endpoint}"
    params = {'q': query, 'sort': 'end', 'order': 'asc'}
    
    try:
        time.sleep(random.uniform(1, 2))
        response = scraper.get(url, params=params)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # --- PLATFORM-SPECIFIC SELECTOR CHAIN ---
        items = []
        
        # Priority 1: Yahoo Auctions (auction listings)
        if platform_name == "Yahoo Auctions":
            items = soup.select('#yahoo-search-results .yahoo-search-result')
        
        # Priority 2: Mercari/Rakuma/Shopping (Product listings using common retail selectors)
        if not items:
            product_container = soup.select_one('#productsContainer') 
            if product_container:
                items = product_container.select('.product')
        
        # Fallback for Yahoo Auctions selector if the above generic container wasn't found/used
        if not items:
            items = soup.select('#yahoo-search-results .yahoo-search-result')

        
        results = []
        
        for item in items:
            # Extract basic data 
            title_tag = item.select_one('.item-title') 
            if not title_tag: continue
            
            title = title_tag.text.strip()
            
            # Link extraction 
            link_tag = item.select_one('a.product-item') 
            link_href = link_tag['href'] if link_tag and 'href' in link_tag.attrs else ''
            link = "https://zenmarket.jp/en/" + link_href if link_href and not link_href.startswith('http') else link_href
            
            # Image extraction 
            img_tag = item.select_one('.img-wrap img')
            img_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else "https://placehold.co/100x100/CCCCCC/000000?text=No+Image"

            # Price extraction 
            price_tag = item.select_one('.price .amount') 
            price_jpy = 0
            price_eur = 0.0
            
            if price_tag and 'data-jpy' in price_tag.attrs:
                price_jpy = float(price_tag['data-jpy'].replace('¥','').replace(',',''))
                price_eur = round(price_jpy / eur_to_jpy_rate, 2)
            
            # Apply Qualification Logic (Updated with Max JPY Ceiling)
            is_valid, status_reason = is_qualified(title, price_jpy, min_jpy_floor, max_jpy_ceiling, negative_keywords)
            
            results.append({
                "Platform": platform_name,
                "Target Model": query,
                "Min EUR Floor (€)": min_eur_floor,
                "Max EUR Ceiling (€)": max_eur_ceiling, # Added Max EUR Ceiling to results
                "Min JPY Floor (Internal)": int(min_jpy_floor),
                "Qualified": is_valid,
                "Status/Reason": status_reason,
                "Title": title,
                "Price JPY": price_jpy,
                "Price EUR (€)": price_eur, 
                "Image URL": img_url,
                "ZenMarket Link": link,
                "Source Query": query
            })
            
        return pd.DataFrame(results, columns=[
            "Platform", "Target Model", "Min EUR Floor (€)", "Max EUR Ceiling (€)", "Min JPY Floor (Internal)", 
            "Qualified", "Status/Reason", "Title", "Price JPY", "Price EUR (€)", "Image URL", "ZenMarket Link", "Source Query"
        ])

    except Exception as e:
        st.warning(f"Connection/Parsing Error on {platform_name} for '{query}': {e}")
        return pd.DataFrame(columns=REQUIRED_COLUMNS) 


# --- 3. STREAMLIT UI & EXPORT ---

st.title("🕵️ ZenMarket Multi-Platform Data Scout")
st.caption(f"Current Conversion Rate: 1 EUR = **{st.session_state['eur_to_jpy']:.2f}** JPY (Editable in Sidebar)")

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.header("1. Exchange Rate & Filters")
    
    # Dynamic Exchange Rate Input
    st.session_state['eur_to_jpy'] = st.number_input(
        "EUR to JPY Rate (1 EUR = X JPY)", 
        value=st.session_state['eur_to_jpy'], 
        min_value=100.0, 
        max_value=250.0, 
        step=0.1,
        format="%.3f",
        key="exchange_rate_input",
        help="Adjust this rate to reflect current market conditions."
    )
    
    # Dynamic Negative Keywords Input
    st.header("2. Exclusion Keywords")
    st.session_state['neg_keywords_str'] = st.text_area(
        "Keywords to Exclude (One per line)",
        value=st.session_state['neg_keywords_str'],
        height=200,
        help="List specific words (e.g., 'glass', 'parts', 'ladies') that, if found in the title, will reject the listing."
    )
    
    st.header("3. Scraper Control")
    if st.button("🚀 Launch Scouting", type="primary", use_container_width=True):
        if not st.session_state['target_df'].empty and all(st.session_state['target_df']['Search Query'].astype(str).str.len() > 0):
             st.session_state['do_scrape'] = True
             st.session_state['results_df'] = pd.DataFrame() # Clear old results
        else:
            st.error("Please ensure the table has valid search queries.")
            st.session_state['do_scrape'] = False

    if st.button("🔄 Reset Targets", use_container_width=True):
        st.session_state['results_df'] = pd.DataFrame()
        st.session_state['do_scrape'] = False
        st.session_state['target_df'] = get_default_targets() 
        st.session_state['neg_keywords_str'] = "\n".join(DEFAULT_NEGATIVE_KEYWORDS)
        st.session_state['eur_to_jpy'] = DEFAULT_EUR_TO_JPY_RATE
        st.experimental_rerun()


# --- DYNAMIC TARGET EDITOR (Main Area) ---
st.header("1. Define Target Watches")
st.markdown("Enter search queries and the minimum/maximum EUR prices for acceptable listings.")

# Ensure the columns needed for the data editor are present before display
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
        "Model Name": st.column_config.TextColumn("Model Name (Label)", required=True),
        "Search Query": st.column_config.TextColumn("Search Query (ZenMarket)", required=True, help="The exact query string for ZenMarket."),
        "Min EUR Floor (€)": st.column_config.NumberColumn("Min EUR Floor (€)", required=True, help="Minimum EUR price to filter out parts/junk."),
        "Max EUR Ceiling (€)": st.column_config.NumberColumn("Max EUR Ceiling (€)", required=True, help="Maximum EUR price for accepted listings."), # NEW COLUMN
    }
)

# Re-add Market Price column to edited data, defaulting to zero if missing
if 'Market Price EUR (€)' not in edited_df.columns:
    edited_df['Market Price EUR (€)'] = 0

st.session_state['target_df'] = edited_df.copy() 


# Data aggregation and processing
if 'do_scrape' in st.session_state and st.session_state['do_scrape']:
    all_results = []
    
    # Process dynamic keywords into a list
    current_neg_keywords = [k.strip().lower() for k in st.session_state['neg_keywords_str'].split('\n') if k.strip()]
    
    total_platforms = len(PLATFORM_ENDPOINTS) * len(st.session_state['target_df'])
    progress_bar = st.progress(0, text="Starting scout across platforms...")
    
    step_count = 0
    
    # Loop over targets first
    for index, scout_data in st.session_state['target_df'].iterrows():
        query = scout_data.get("Search Query")
        min_eur_floor = scout_data.get("Min EUR Floor (€)")
        max_eur_ceiling = scout_data.get("Max EUR Ceiling (€)") # NEW: Get ceiling value
        
        # Then loop over all 4 platforms for that target
        for platform_name, endpoint in PLATFORM_ENDPOINTS.items():
            
            progress_bar.progress(step_count / total_platforms, text=f"Scouting **{platform_name}** for **{query}**...")
            
            df_results = run_platform_scrape(
                platform_name, 
                endpoint, 
                query, 
                min_eur_floor, 
                max_eur_ceiling, # Pass new ceiling value
                st.session_state['eur_to_jpy'], 
                current_neg_keywords
            )
            
            if not df_results.empty:
                all_results.append(df_results)
            
            step_count += 1
    
    progress_bar.progress(1.0, text="Scouting Complete.")

    if all_results:
        final_df = pd.concat(all_results, ignore_index=True)
        # Drop irrelevant columns from the final DF structure
        final_df = final_df.drop(columns=['Landed Cost EUR (€)', 'Potential Profit (€)', 'Market Price EUR (€)'], errors='ignore')
        
        st.session_state['results_df'] = final_df
        st.session_state['do_scrape'] = False 
    else:
        st.warning("Scouting completed, but no data was returned for any query/platform combination.")
        st.session_state['do_scrape'] = False


# Display Results and Export Button
if not st.session_state['results_df'].empty:
    df_display = st.session_state['results_df']
    
    # Separate Qualified from Rejected for display
    qualified_df = df_display[df_display['Qualified'] == True].copy()
    rejected_df = df_display[df_display['Qualified'] == False].copy()
    
    st.header("Scouting Results")
    st.success(f"Found {len(qualified_df)} Qualified Watches ready for Manual Review.")
    
    tab1, tab2 = st.tabs(["✅ Qualified Watches", "🗑️ Rejected Items"])
    
    # Final data presentation for Qualified Listings
    qualified_display = qualified_df.drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)']).rename(columns={'Min EUR Floor (€)': 'Min Filter EUR (€)'})
    
    # Format Price EUR for display
    qualified_display['Price EUR (€)'] = qualified_display['Price EUR (€)'].apply(lambda x: f"€{x:,.2f}" if x is not None else 'N/A')
    
    # Configure 'Image URL' column to display images
    qualified_column_config = {
        "Image URL": st.column_config.ImageColumn(
            "Image", width="small"
        ),
        "ZenMarket Link": st.column_config.LinkColumn(
            "Link", width="medium", display_text="Open Listing"
        )
    }

    with tab1:
        # Include Max EUR Ceiling in the display
        st.dataframe(
            qualified_display,
            column_order=["Platform", "Target Model", "Image URL", "Title", "Price JPY", "Price EUR (€)", "Min Filter EUR (€)", "Max EUR Ceiling (€)", "ZenMarket Link"],
            column_config=qualified_column_config,
            hide_index=True,
            use_container_width=True
        )

    with tab2:
        st.info(f"Filtered out {len(rejected_df)} items.")
        rejected_display = rejected_df.drop(columns=['Qualified', 'Image URL', 'Min EUR Floor (€)']).rename(columns={'Min JPY Floor (Internal)': 'Min JPY Floor'})
        
        # Include Max EUR Ceiling in the rejected display
        rejected_display['Max EUR Ceiling (€)'] = rejected_df['Max EUR Ceiling (€)']
        
        st.dataframe(
            rejected_display,
            column_order=["Platform", "Target Model", "Status/Reason", "Title", "Price JPY", "Min JPY Floor", "Max EUR Ceiling (€)", "ZenMarket Link"],
            column_config={"ZenMarket Link": st.column_config.LinkColumn("Link", display_text="Open Listing")},
            hide_index=True,
            use_container_width=True
        )

    # XLSX Export Functionality
    def to_excel(df: pd.DataFrame) -> bytes:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            
            # Prepare Qualified data for export (including Max EUR Ceiling)
            qualified_export_df = df[df['Qualified'] == True].drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)']).rename(columns={'Min EUR Floor (€)': 'Min Filter EUR (€)'})
            qualified_export_df.to_excel(writer, sheet_name='Qualified Listings', index=False)
            
            # Prepare Rejected data for export (including Max EUR Ceiling)
            rejected_export_df = df[df['Qualified'] == False].drop(columns=['Qualified', 'Source Query', 'Min JPY Floor (Internal)', 'Image URL']).rename(columns={'Min EUR Floor (€)': 'Min Filter EUR (€)'})
            rejected_export_df.to_excel(writer, sheet_name='Rejected Candidates', index=False)
            
        processed_data = output.getvalue()
        return processed_data

    xlsx_data = to_excel(df_display) 
    
    st.download_button(
        label="Download All Qualified Listings (XLSX)",
        data=xlsx_data,
        file_name='zenmarket_watch_scout_results_dynamic_multi.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type="secondary",
        use_container_width=True
    )

else:
    st.info("👈 Define your targets in the table above and click 'Launch Scouting' to begin.")