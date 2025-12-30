from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import re
from urllib.parse import urlparse, parse_qs
import os
import json
import sqlite3
from datetime import datetime, timedelta
import os
from bs4 import BeautifulSoup
import threading
import time

# Import alternative detection methods (will be set up after API config is defined)
ALTERNATIVE_METHODS_AVAILABLE = False
try:
    import alternative_detection_methods
    ALTERNATIVE_METHODS_AVAILABLE = True
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

# Giphy API configuration
# API Key is optional - if not provided, we'll use web scraping as fallback
# Get your API key from: https://developers.giphy.com/
GIPHY_API_KEY = os.environ.get('GIPHY_API_KEY', 'L8eXbxrbPETZxlvgXN9kIEzQ55Df04v0')  # Your API key
GIPHY_API_BASE = 'https://api.giphy.com/v1'
USE_API = os.environ.get('USE_GIPHY_API', 'true').lower() == 'true'

# Database configuration
# On Vercel, use /tmp directory since filesystem is read-only except /tmp
# Check if running on Vercel by checking for VERCEL environment variable
if os.environ.get('VERCEL'):
    DB_NAME = '/tmp/giphy_tracking.db'
else:
    DB_NAME = 'giphy_tracking.db'

# Initialize database
def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Table for channels/users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            username TEXT,
            user_id TEXT,
            display_name TEXT,
            profile_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Table for GIFs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gifs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gif_id TEXT UNIQUE NOT NULL,
            channel_id TEXT NOT NULL,
            title TEXT,
            url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        )
    ''')
    
    # Table for view history (daily tracking)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS view_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gif_id TEXT NOT NULL,
            view_count INTEGER NOT NULL,
            recorded_date DATE NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gif_id) REFERENCES gifs(gif_id),
            UNIQUE(gif_id, recorded_date)
        )
    ''')
    
    # Create indexes for faster queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gif_id ON gifs(gif_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_channel_id ON gifs(channel_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_view_history_gif_date ON view_history(gif_id, recorded_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_view_history_date ON view_history(recorded_date)')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_database()

# Set up alternative detection methods if available
if ALTERNATIVE_METHODS_AVAILABLE:
    try:
        alternative_detection_methods.set_api_config(GIPHY_API_BASE, GIPHY_API_KEY)
    except:
        pass

# Proxy configuration for multi-location checks
PROXY_CONFIGS = {
    'india': None,  # Set your India proxy here if available: 'http://proxy_india:port'
    'usa': None,    # Set your USA proxy here if available: 'http://proxy_usa:port'
    # Example format: 'http://username:password@proxy.example.com:8080'
    # Or: 'socks5://proxy.example.com:1080'
}

# Alternative: Use VPN services or proxy rotation services
# You can also use services like Bright Data, Oxylabs, etc.

def extract_views_from_nested_dict(data, max_depth=10):
    """
    Recursively search a nested dictionary for view count.
    
    Args:
        data: Dictionary or list to search
        max_depth: Maximum recursion depth
    
    Returns:
        View count as integer, or None if not found
    """
    if max_depth <= 0:
        return None
    
    if isinstance(data, dict):
        # Check common keys first
        for key in ['views', 'view_count', 'viewCount', 'view_count_total', 'total_views']:
            if key in data:
                try:
                    value = data[key]
                    if isinstance(value, (int, str)):
                        views = int(value)
                        if views > 0:
                            return views
                except:
                    pass
        
        # Recursively search nested structures
        for value in data.values():
            if isinstance(value, (dict, list)):
                result = extract_views_from_nested_dict(value, max_depth - 1)
                if result:
                    return result
    
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                result = extract_views_from_nested_dict(item, max_depth - 1)
                if result:
                    return result
    
    return None

def get_gif_url_from_db(gif_id):
    """Get stored GIF URL from database"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT url FROM gifs WHERE gif_id = ?', (gif_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else None
    except:
        return None

def scrape_gif_views_with_proxy(gif_id, proxy=None, location='default', gif_url=None):
    """
    Scrape actual view count from a Giphy GIF page using a proxy.
    First tries Giphy API detail endpoint, then falls back to HTML scraping.
    Returns view count or None if unable to fetch.
    
    Args:
        gif_id: Giphy GIF ID
        proxy: Proxy URL to use (e.g., 'http://proxy:port')
        location: Location identifier for logging (e.g., 'india', 'usa')
        gif_url: Optional full GIF URL from API (more reliable than constructing)
    """
    try:
        # Method 0: Try Giphy API detail endpoint first (fastest and most reliable)
        if GIPHY_API_KEY and GIPHY_API_KEY != 'dc6zaTOxFJmzC':
            try:
                gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
                gif_detail_params = {'api_key': GIPHY_API_KEY}
                gif_detail_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
                
                if gif_detail_response.status_code == 200:
                    gif_detail = gif_detail_response.json().get('data', {})
                    views = gif_detail.get('views')
                    if views is not None:
                        try:
                            views_int = int(views)
                            if views_int > 0:
                                print(f"  [{location}] Found views via API: {views_int:,}")
                                return views_int
                        except:
                            pass
            except Exception as e:
                # API failed, continue to HTML scraping
                pass
        
        # Try multiple URL formats for HTML scraping
        url_to_try = []
        
        # 1. Use provided URL if available (most reliable)
        if gif_url:
            url_to_try.append(gif_url)
        
        # 2. Get URL from database
        db_url = get_gif_url_from_db(gif_id)
        if db_url and db_url not in url_to_try:
            url_to_try.append(db_url)
        
        # 3. Construct URL (fallback - may not work)
        constructed_url = f"https://giphy.com/gifs/{gif_id}"
        if constructed_url not in url_to_try:
            url_to_try.append(constructed_url)
        
        # 4. If provided URL is a sticker, also try the /gifs/ version
        if gif_url and '/stickers/' in gif_url:
            gifs_version = gif_url.replace('/stickers/', '/gifs/')
            if gifs_version not in url_to_try:
                url_to_try.append(gifs_version)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        proxies = None
        if proxy:
            proxies = {
                'http': proxy,
                'https': proxy
            }
        
        # Try each URL until one works
        for test_url in url_to_try:
            try:
                response = requests.get(test_url, headers=headers, proxies=proxies, timeout=15, allow_redirects=True)
            except Exception as e:
                print(f"  [{location}] Request error for {test_url}: {str(e)[:50]}")
                continue  # Try next URL
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                text_content = response.text  # Store for pattern matching
                
                # Method 1: Look for view count in meta tags (multiple variations)
                meta_properties = ['giphy:views', 'og:views', 'twitter:views']
                for prop in meta_properties:
                    meta_views = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
                    if meta_views and meta_views.get('content'):
                        try:
                            views = int(meta_views.get('content'))
                            if views > 0:
                                print(f"  [{location}] Found views via meta tag ({prop}): {views:,}")
                                return views
                        except:
                            pass
                
                # Method 1b: Look for views in data attributes
                try:
                    elements_with_views = soup.find_all(attrs={'data-views': True})
                    for elem in elements_with_views:
                        try:
                            views = int(elem.get('data-views'))
                            if views > 0:
                                print(f"  [{location}] Found views via data-views attribute: {views:,}")
                                return views
                        except:
                            continue
                except:
                    pass
                
                # Method 2: Look for view count in ALL script tags (not just application/json)
                # Giphy embeds data in regular script tags, often in __NEXT_DATA__ or similar
                all_scripts = soup.find_all('script')
                for script in all_scripts:
                    if not script.string:
                        continue
                    
                    script_content = script.string
                    
                    # Method 2a: Look for __NEXT_DATA__, window.__INITIAL_STATE__, or similar data structures
                    data_indicators = ['__NEXT_DATA__', 'pageProps', '__INITIAL_STATE__', 'window.__data', 'giphyData']
                    if any(indicator in script_content for indicator in data_indicators):
                        # Try to extract JSON from the script
                        try:
                            # Try to find JSON object assignments like: window.__INITIAL_STATE__ = {...}
                            json_patterns = [
                                r'__NEXT_DATA__\s*=\s*(\{.*?\})',
                                r'__INITIAL_STATE__\s*=\s*(\{.*?\})',
                                r'window\.__data\s*=\s*(\{.*?\})',
                                r'giphyData\s*=\s*(\{.*?\})',
                            ]
                            
                            for pattern in json_patterns:
                                json_match = re.search(pattern, script_content, re.DOTALL)
                                if json_match:
                                    try:
                                        data = json.loads(json_match.group(1))
                                        views = extract_views_from_nested_dict(data)
                                        if views:
                                            print(f"  [{location}] Found views via data structure: {views:,}")
                                            return views
                                    except:
                                        pass
                            
                            # If no assignment pattern, try to find any large JSON object
                            json_match = re.search(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', script_content, re.DOTALL)
                            if json_match:
                                try:
                                    # Try to parse progressively larger chunks
                                    json_str = json_match.group(1)
                                    # Try full match first
                                    try:
                                        data = json.loads(json_str)
                                        views = extract_views_from_nested_dict(data)
                                        if views:
                                            print(f"  [{location}] Found views via JSON object: {views:,}")
                                            return views
                                    except:
                                        # Try to find a valid JSON subset
                                        for i in range(len(json_str), 100, -100):
                                            try:
                                                data = json.loads(json_str[:i] + '}')
                                                views = extract_views_from_nested_dict(data)
                                                if views:
                                                    print(f"  [{location}] Found views via JSON subset: {views:,}")
                                                    return views
                                            except:
                                                continue
                                except:
                                    pass
                            
                            # Also search for views patterns directly in script content
                            view_patterns = [
                                r'["\']views["\']\s*:\s*(\d+)',
                                r'["\']viewCount["\']\s*:\s*(\d+)',
                                r'["\']view_count["\']\s*:\s*(\d+)',
                                r'["\']totalViews["\']\s*:\s*(\d+)',
                                r'views["\']?\s*[:=]\s*(\d+)',
                            ]
                            for pattern in view_patterns:
                                matches = re.findall(pattern, script_content, re.IGNORECASE)
                                if matches:
                                    try:
                                        # Take the largest number (usually the actual view count)
                                        view_nums = [int(m) for m in matches if int(m) > 10]
                                        if view_nums:
                                            views = max(view_nums)
                                            print(f"  [{location}] Found views in script via regex: {views:,}")
                                            return views
                                    except:
                                        continue
                        except:
                            continue
                    
                    # Method 2b: Try parsing as JSON if type is application/json
                    if script.get('type') == 'application/json':
                        try:
                            data = json.loads(script_content)
                            views = extract_views_from_nested_dict(data)
                            if views:
                                print(f"  [{location}] Found views via JSON script: {views:,}")
                                return views
                        except:
                            continue
                    
                    # Method 2c: Search for view patterns in any script tag
                    try:
                        view_patterns = [
                            r'["\']views["\']\s*:\s*(\d+)',
                            r'["\']viewCount["\']\s*:\s*(\d+)',
                            r'["\']view_count["\']\s*:\s*(\d+)',
                            r'views["\']?\s*[:=]\s*(\d+)',
                        ]
                        for pattern in view_patterns:
                            matches = re.findall(pattern, script_content, re.IGNORECASE)
                            if matches:
                                try:
                                    view_nums = [int(m) for m in matches if int(m) > 10]
                                    if view_nums:
                                        views = max(view_nums)
                                        print(f"  [{location}] Found views in script tag via pattern: {views:,}")
                                        return views
                                except:
                                    continue
                    except:
                        continue
                
                # Method 3: Search for view count patterns in entire HTML/text
                # Look for common patterns with better regex
                view_patterns = [
                    r'["\']views["\']\s*:\s*(\d+)',  # "views": 12345
                    r'["\']viewCount["\']\s*:\s*(\d+)',  # "viewCount": 12345
                    r'["\']view_count["\']\s*:\s*(\d+)',  # "view_count": 12345
                    r'["\']totalViews["\']\s*:\s*(\d+)',  # "totalViews": 12345
                    r'view[_-]?count["\']?\s*[:=]\s*(\d+)',  # view_count: 12345
                    r'(\d{1,3}(?:,\d{3})*)\s*views?',  # 12,345 views
                    r'<span[^>]*class[^>]*view[^>]*>(\d+(?:,\d+)*)',  # <span class="views">12345
                    r'<div[^>]*class[^>]*view[^>]*>(\d+(?:,\d+)*)',  # <div class="views">12345
                    r'data-views=["\'](\d+)',  # data-views="12345"
                    r'views["\']?\s*[:=]\s*["\']?(\d+)',  # views: "12345" or views: 12345
                    r'(\d+)\s*views?',  # 12345 views (standalone)
                    r'view[_-]?count["\']?\s*[:=]\s*["\']?(\d+)',  # view_count: "12345"
                ]
                
                all_matches = []
                for pattern in view_patterns:
                    matches = re.findall(pattern, text_content, re.IGNORECASE)
                    all_matches.extend(matches)
                
                if all_matches:
                    try:
                        # Clean and filter view numbers
                        view_numbers = []
                        for match in all_matches:
                            view_str = str(match).replace(',', '').replace('"', '').replace("'", "").strip()
                            try:
                                view_num = int(view_str)
                                if view_num > 10:  # Filter out small numbers that might be IDs
                                    view_numbers.append(view_num)
                            except:
                                continue
                        
                        if view_numbers:
                            # Use the largest reasonable number (likely the actual view count)
                            # But filter out extremely large numbers that might be timestamps or IDs
                            reasonable_views = [v for v in view_numbers if v < 1000000000]  # Less than 1 billion
                            if reasonable_views:
                                views = max(reasonable_views)
                                print(f"  [{location}] Found views via pattern matching: {views:,}")
                                return views
                    except:
                        pass
                
                # Method 4: Try to find views in visible text elements (spans, divs with text)
                # This is important because Giphy displays "X Views" prominently on the page
                try:
                    # Look for elements that might contain view counts - search all text content
                    # Pattern: "6,943 Views" or "6943 Views" or "6,943 views"
                    view_text_patterns = [
                        r'(\d{1,3}(?:,\d{3})*)\s+views?',  # "6,943 Views" or "6943 views"
                        r'(\d+)\s+views?',  # "6943 Views"
                        r'views?[:\s]+(\d{1,3}(?:,\d{3})*)',  # "Views: 6,943" or "Views 6,943"
                    ]
                    
                    # Search in all text content
                    page_text = soup.get_text() if hasattr(soup, 'get_text') else str(soup)
                    for pattern in view_text_patterns:
                        matches = re.findall(pattern, page_text, re.IGNORECASE)
                        if matches:
                            try:
                                # Get all potential view numbers
                                view_numbers = []
                                for match in matches:
                                    view_str = str(match).replace(',', '').strip()
                                    try:
                                        view_num = int(view_str)
                                        if 10 < view_num < 1000000000:  # Reasonable range
                                            view_numbers.append(view_num)
                                    except:
                                        continue
                                
                                if view_numbers:
                                    # Use the largest number (likely the actual view count)
                                    views = max(view_numbers)
                                    print(f"  [{location}] Found views in page text: {views:,}")
                                    return views
                            except:
                                continue
                    
                    # Also search in specific elements that commonly contain view counts
                    view_elements = soup.find_all(['span', 'div', 'p', 'h1', 'h2', 'h3', 'strong', 'b'], 
                                                   string=re.compile(r'\d+.*views?', re.IGNORECASE))
                    for elem in view_elements:
                        text = elem.get_text() if hasattr(elem, 'get_text') else str(elem)
                        view_match = re.search(r'(\d{1,3}(?:,\d{3})*)\s*views?', text, re.IGNORECASE)
                        if view_match:
                            try:
                                views = int(view_match.group(1).replace(',', ''))
                                if views > 10:
                                    print(f"  [{location}] Found views in visible text element: {views:,}")
                                    return views
                            except:
                                continue
                except:
                    pass
                
                print(f"  [{location}] Could not extract views from {test_url}")
                # Continue to next URL format if this one didn't work
                continue
            else:
                # URL returned non-200 status, try next one
                continue
        
        # All URLs failed
        print(f"  [{location}] All URL formats failed for GIF {gif_id}")
            
    except requests.exceptions.ProxyError as e:
        print(f"  [{location}] Proxy error: {str(e)}")
    except Exception as e:
        print(f"  [{location}] Error scraping views: {str(e)}")
    
    return None

def check_views_multiple_locations(gif_id, sample_count=3):
    """
    Check view counts from multiple locations (India, USA) to get accurate data.
    Returns a dictionary with view counts from different locations.
    
    Args:
        gif_id: Giphy GIF ID
        sample_count: Number of samples to take (checks multiple times for accuracy)
    """
    results = {
        'gif_id': gif_id,
        'locations': {},
        'average_views': 0,
        'view_variance': 0,
        'success': False
    }
    
    location_views = {
        'default': [],
        'india': [],
        'usa': []
    }
    
    # Check from default location (no proxy)
    print(f"Checking views for GIF {gif_id}...")
    for i in range(sample_count):
        views = scrape_gif_views_with_proxy(gif_id, proxy=None, location='default')
        if views is not None:
            location_views['default'].append(views)
        if i < sample_count - 1:
            time.sleep(1)  # Small delay between requests
    
    # Check from India proxy (if configured)
    if PROXY_CONFIGS.get('india'):
        print(f"  Checking from India proxy...")
        for i in range(sample_count):
            views = scrape_gif_views_with_proxy(gif_id, proxy=PROXY_CONFIGS['india'], location='india')
            if views is not None:
                location_views['india'].append(views)
            if i < sample_count - 1:
                time.sleep(1)
    else:
        print(f"  India proxy not configured, skipping...")
    
    # Check from USA proxy (if configured)
    if PROXY_CONFIGS.get('usa'):
        print(f"  Checking from USA proxy...")
        for i in range(sample_count):
            views = scrape_gif_views_with_proxy(gif_id, proxy=PROXY_CONFIGS['usa'], location='usa')
            if views is not None:
                location_views['usa'].append(views)
            if i < sample_count - 1:
                time.sleep(1)
    else:
        print(f"  USA proxy not configured, skipping...")
    
    # Calculate averages for each location
    for location, views_list in location_views.items():
        if views_list:
            avg_views = sum(views_list) / len(views_list)
            results['locations'][location] = {
                'views': views_list,
                'average': avg_views,
                'min': min(views_list),
                'max': max(views_list),
                'count': len(views_list)
            }
    
    # Calculate overall average
    all_views = []
    for location_data in results['locations'].values():
        all_views.extend(location_data['views'])
    
    if all_views:
        results['average_views'] = sum(all_views) / len(all_views)
        results['min_views'] = min(all_views)
        results['max_views'] = max(all_views)
        results['success'] = True
        
        # Calculate variance (how much views vary)
        if len(all_views) > 1:
            mean = results['average_views']
            variance = sum((x - mean) ** 2 for x in all_views) / len(all_views)
            results['view_variance'] = variance
    
    return results

def update_gif_views_with_location_check(gif_id, store_location=True):
    """
    Update view count for a GIF using multi-location checks.
    Stores the view count with location information.
    
    Args:
        gif_id: Giphy GIF ID
        store_location: Whether to store location-specific data
    """
    try:
        # Check views from multiple locations
        location_results = check_views_multiple_locations(gif_id, sample_count=2)
        
        if location_results['success']:
            # Store the average view count
            store_view_count(gif_id, int(location_results['average_views']))
            
            # Optionally store location-specific data in a separate table or as metadata
            if store_location:
                # Store in view_history with location metadata (you can extend the schema if needed)
                # For now, we store the average
                pass
            
            return {
                'gif_id': gif_id,
                'views': int(location_results['average_views']),
                'locations': location_results['locations'],
                'success': True
            }
        else:
            return {
                'gif_id': gif_id,
                'views': None,
                'success': False,
                'error': 'Could not fetch views from any location'
            }
    except Exception as e:
        return {
            'gif_id': gif_id,
            'views': None,
            'success': False,
            'error': str(e)
        }

def analyze_channel_status_with_location_checks(channel_id, days=2):
    """
    Analyze channel status by checking view trends from multiple locations over the last N days.
    
    This function:
    1. Gets view history for the last N days
    2. Checks current views from multiple locations (India, USA)
    3. Compares trends to determine if views are increasing
    4. Returns accurate status: WORKING, SHADOW BANNED, or BANNED
    """
    try:
        # Get all GIFs for this channel
        gifs = get_channel_gifs(channel_id)
        
        if not gifs:
            return {
                'status': 'banned',
                'reason': 'No GIFs found for channel',
                'banned': True,
                'working': False,
                'shadow_banned': False
            }
        
        gif_ids = [gif['gif_id'] for gif in gifs]
        
        # Get view history for the last N days
        historical_views = {}
        current_views = {}
        
        print(f"\n{'='*60}")
        print(f"ANALYZING CHANNEL: {channel_id} (Multi-Location View Check)")
        print(f"{'='*60}")
        print(f"Total GIFs: {len(gif_ids)}")
        print(f"Checking views over last {days} days...\n")
        
        # Check historical views from database
        for gif_id in gif_ids:
            history = get_gif_view_history(gif_id, days=days)
            if history:
                # Get views from 2 days ago and today
                historical_views[gif_id] = history
        
        # Check current views from multiple locations for each GIF
        gifs_with_increasing_views = 0
        gifs_with_stagnant_views = 0
        gifs_with_decreasing_views = 0
        gifs_with_no_views = 0
        total_views_increase = 0
        
        for idx, gif_id in enumerate(gif_ids[:20], 1):  # Limit to first 20 GIFs for performance
            print(f"[{idx}/{min(20, len(gif_ids))}] Checking GIF: {gif_id}")
            
            # Get current views from multiple locations
            location_check = check_views_multiple_locations(gif_id, sample_count=1)
            current_views[gif_id] = location_check
            
            if location_check['success']:
                current_view = int(location_check['average_views'])
                
                # Compare with historical data
                if gif_id in historical_views and len(historical_views[gif_id]) >= 1:
                    # Get the oldest view count we have
                    oldest_view = historical_views[gif_id][0]['view_count']
                    view_change = current_view - oldest_view
                    view_change_percent = (view_change / oldest_view * 100) if oldest_view > 0 else 0
                    
                    print(f"  Current: {current_view:,} | Historical: {oldest_view:,} | Change: {view_change:+,} ({view_change_percent:+.1f}%)")
                    
                    # Determine trend
                    if view_change > 0 and view_change_percent > 5:  # 5% increase threshold
                        gifs_with_increasing_views += 1
                        total_views_increase += view_change
                        print(f"  ✓ Views INCREASING")
                    elif view_change < 0 and abs(view_change_percent) > 5:
                        gifs_with_decreasing_views += 1
                        print(f"  ✗ Views DECREASING")
                    else:
                        gifs_with_stagnant_views += 1
                        print(f"  - Views STAGNANT")
                else:
                    # No historical data, but we have current views
                    if current_view > 0:
                        gifs_with_increasing_views += 1  # Optimistic: if we can see views, it's working
                        print(f"  ✓ Has views (no historical data)")
                    else:
                        gifs_with_no_views += 1
                        print(f"  ✗ No views detected")
            else:
                gifs_with_no_views += 1
                print(f"  ✗ Could not fetch views")
            
            # Store current view for future comparison
            if location_check['success']:
                store_view_count(gif_id, int(location_check['average_views']))
            
            if idx < min(20, len(gif_ids)):
                time.sleep(2)  # Delay between GIFs to avoid rate limiting
        
        # Determine overall status
        total_checked = gifs_with_increasing_views + gifs_with_stagnant_views + gifs_with_decreasing_views + gifs_with_no_views
        
        print(f"\n{'='*60}")
        print(f"ANALYSIS RESULTS:")
        print(f"  GIFs with increasing views: {gifs_with_increasing_views}")
        print(f"  GIFs with stagnant views: {gifs_with_stagnant_views}")
        print(f"  GIFs with decreasing views: {gifs_with_decreasing_views}")
        print(f"  GIFs with no views: {gifs_with_no_views}")
        print(f"  Total views increase: {total_views_increase:,}")
        print(f"{'='*60}\n")
        
        # Decision logic
        if total_checked == 0:
            return {
                'status': 'banned',
                'reason': 'Could not check any GIFs',
                'banned': True,
                'working': False,
                'shadow_banned': False
            }
        
        # If majority of GIFs have increasing views → WORKING
        if gifs_with_increasing_views > (total_checked * 0.3):  # At least 30% increasing
            return {
                'status': 'working',
                'reason': f'{gifs_with_increasing_views}/{total_checked} GIFs have increasing views (views growing)',
                'banned': False,
                'working': True,
                'shadow_banned': False,
                'stats': {
                    'increasing': gifs_with_increasing_views,
                    'stagnant': gifs_with_stagnant_views,
                    'decreasing': gifs_with_decreasing_views,
                    'no_views': gifs_with_no_views,
                    'total_checked': total_checked
                }
            }
        
        # If no views or all stagnant → SHADOW BANNED
        if gifs_with_no_views >= (total_checked * 0.7) or (gifs_with_stagnant_views >= (total_checked * 0.7) and gifs_with_increasing_views == 0):
            return {
                'status': 'shadow_banned',
                'reason': f'{gifs_with_no_views + gifs_with_stagnant_views}/{total_checked} GIFs have no views or stagnant views (not getting engagement)',
                'banned': False,
                'working': False,
                'shadow_banned': True,
                'stats': {
                    'increasing': gifs_with_increasing_views,
                    'stagnant': gifs_with_stagnant_views,
                    'decreasing': gifs_with_decreasing_views,
                    'no_views': gifs_with_no_views,
                    'total_checked': total_checked
                }
            }
        
        # Default: if we have some views but not clearly increasing → SHADOW BANNED (conservative)
        return {
            'status': 'shadow_banned',
            'reason': f'Mixed results: {gifs_with_increasing_views} increasing, {gifs_with_stagnant_views} stagnant, {gifs_with_no_views} no views',
            'banned': False,
            'working': False,
            'shadow_banned': True,
            'stats': {
                'increasing': gifs_with_increasing_views,
                'stagnant': gifs_with_stagnant_views,
                'decreasing': gifs_with_decreasing_views,
                'no_views': gifs_with_no_views,
                'total_checked': total_checked
            }
        }
        
    except Exception as e:
        print(f"Error in location-based analysis: {str(e)}")
        return {
            'status': 'error',
            'reason': str(e),
            'banned': False,
            'working': False,
            'shadow_banned': False
        }

def format_number(num):
    """Format large numbers with K, M, B suffixes"""
    if num >= 1000000000:
        return f"{num / 1000000000:.1f}B"
    elif num >= 1000000:
        return f"{num / 1000000:.1f}M"
    elif num >= 1000:
        return f"{num / 1000:.1f}K"
    return str(num)

def scrape_gif_views(gif_id):
    """Legacy function - now calls scrape_gif_views_with_proxy with no proxy for backward compatibility"""
    return scrape_gif_views_with_proxy(gif_id, proxy=None, location='default')

def store_channel_data(channel_id, username=None, user_id=None, display_name=None, profile_url=None):
    """Store or update channel data in database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO channels (channel_id, username, user_id, display_name, profile_url, last_updated)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (channel_id, username, user_id, display_name, profile_url))
    
    conn.commit()
    conn.close()

def store_gif_data(gif_id, channel_id, title=None, url=None):
    """Store or update GIF data in database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO gifs (gif_id, channel_id, title, url)
        VALUES (?, ?, ?, ?)
    ''', (gif_id, channel_id, title, url))
    
    conn.commit()
    conn.close()

def store_view_count(gif_id, view_count, recorded_date=None):
    """Store view count for a GIF on a specific date"""
    if recorded_date is None:
        recorded_date = datetime.now().date()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO view_history (gif_id, view_count, recorded_date)
        VALUES (?, ?, ?)
    ''', (gif_id, view_count, recorded_date))
    
    conn.commit()
    conn.close()

def get_gif_view_history(gif_id, days=7):
    """Get view history for a GIF over the specified number of days"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    start_date = (datetime.now() - timedelta(days=days)).date()
    
    cursor.execute('''
        SELECT view_count, recorded_date
        FROM view_history
        WHERE gif_id = ? AND recorded_date >= ?
        ORDER BY recorded_date ASC
    ''', (gif_id, start_date))
    
    results = cursor.fetchall()
    conn.close()
    
    return [{'view_count': row[0], 'date': row[1]} for row in results]

def get_channel_gifs(channel_id):
    """Get all GIFs for a channel"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT gif_id, title, url
        FROM gifs
        WHERE channel_id = ?
    ''', (channel_id,))
    
    results = cursor.fetchall()
    conn.close()
    
    return [{'gif_id': row[0], 'title': row[1], 'url': row[2]} for row in results]

def get_latest_views_for_channel(channel_id):
    """Get latest view counts for all GIFs in a channel"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT g.gif_id, g.title, g.url, 
               (SELECT vh.view_count 
                FROM view_history vh 
                WHERE vh.gif_id = g.gif_id 
                ORDER BY vh.recorded_date DESC 
                LIMIT 1) as latest_views
        FROM gifs g
        WHERE g.channel_id = ?
    ''', (channel_id,))
    
    results = cursor.fetchall()
    conn.close()
    
    return [{'gif_id': row[0], 'title': row[1], 'url': row[2], 'views': row[3] or 0} for row in results]

def get_channel_total_views_for_date(channel_id, target_date):
    """
    Get total view count for all GIFs in a channel for a specific date.
    Returns the sum of all views for that date.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all GIF IDs for this channel
    cursor.execute('SELECT gif_id FROM gifs WHERE channel_id = ?', (channel_id,))
    gif_ids = [row[0] for row in cursor.fetchall()]
    
    if not gif_ids:
        conn.close()
        return 0
    
    # Get views for each GIF on the target date
    total_views = 0
    for gif_id in gif_ids:
        cursor.execute('''
            SELECT view_count 
            FROM view_history 
            WHERE gif_id = ? AND recorded_date = ?
            ORDER BY recorded_date DESC 
            LIMIT 1
        ''', (gif_id, target_date))
        
        result = cursor.fetchone()
        if result:
            total_views += result[0]
    
    conn.close()
    return total_views

def get_channel_views_history_graph(channel_id, days=30):
    """
    Get historical view data for a channel formatted for graphing.
    Returns cumulative total views per date (like the Giphy dashboard graph).
    
    Args:
        channel_id: Channel ID to get history for
        days: Number of days to look back (default 30)
    
    Returns:
        Dictionary with:
        - dates: List of date strings (YYYY-MM-DD)
        - total_views: List of cumulative total views for each date
        - data_points: List of {date, views} objects for easy graphing
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all GIF IDs for this channel
    cursor.execute('SELECT gif_id FROM gifs WHERE channel_id = ?', (channel_id,))
    gif_ids = [row[0] for row in cursor.fetchall()]
    
    if not gif_ids:
        conn.close()
        return {
            'dates': [],
            'total_views': [],
            'data_points': [],
            'channel_id': channel_id
        }
    
    # Get all unique dates with view data for this channel
    placeholders = ','.join(['?'] * len(gif_ids))
    cursor.execute(f'''
        SELECT DISTINCT recorded_date
        FROM view_history
        WHERE gif_id IN ({placeholders})
        ORDER BY recorded_date ASC
    ''', gif_ids)
    
    dates = [row[0] for row in cursor.fetchall()]
    
    # Filter to last N days if needed
    if days:
        cutoff_date = (datetime.now() - timedelta(days=days)).date()
        dates = [d for d in dates if d >= cutoff_date]
    
    # Get total views for each date
    data_points = []
    for date in dates:
        total_views = get_channel_total_views_for_date(channel_id, date)
        data_points.append({
            'date': str(date),
            'views': total_views
        })
    
    conn.close()
    
    return {
        'channel_id': channel_id,
        'dates': [str(d) for d in dates],
        'total_views': [dp['views'] for dp in data_points],
        'data_points': data_points,
        'total_data_points': len(data_points)
    }

def get_channel_total_views_24_hours_ago(channel_id):
    """
    Alternative approach: Get total view count for all GIFs from 24 hours ago using timestamps.
    This allows comparison at any time, not just at midnight.
    Returns the sum of all views from approximately 24 hours ago.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all GIF IDs for this channel
    cursor.execute('SELECT gif_id FROM gifs WHERE channel_id = ?', (channel_id,))
    gif_ids = [row[0] for row in cursor.fetchall()]
    
    if not gif_ids:
        conn.close()
        return 0, None
    
    # Calculate timestamp for 24 hours ago
    now = datetime.now()
    twenty_four_hours_ago = now - timedelta(hours=24)
    
    # Get views for each GIF closest to 24 hours ago
    total_views = 0
    earliest_timestamp = None
    
    for gif_id in gif_ids:
        cursor.execute('''
            SELECT view_count, recorded_at
            FROM view_history 
            WHERE gif_id = ? AND recorded_at <= ?
            ORDER BY recorded_at DESC 
            LIMIT 1
        ''', (gif_id, twenty_four_hours_ago.strftime('%Y-%m-%d %H:%M:%S')))
        
        result = cursor.fetchone()
        if result:
            total_views += result[0]
            if earliest_timestamp is None or result[1] < earliest_timestamp:
                earliest_timestamp = result[1]
    
    conn.close()
    return total_views, earliest_timestamp

def get_channel_total_views_48_hours_ago(channel_id):
    """
    Get total view count for all GIFs from 48 hours ago using timestamps.
    This allows comparison over a longer period for better trend detection.
    Returns the sum of all views from approximately 48 hours ago.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all GIF IDs for this channel
    cursor.execute('SELECT gif_id FROM gifs WHERE channel_id = ?', (channel_id,))
    gif_ids = [row[0] for row in cursor.fetchall()]
    
    if not gif_ids:
        conn.close()
        return 0, None
    
    # Calculate timestamp for 48 hours ago
    now = datetime.now()
    forty_eight_hours_ago = now - timedelta(hours=48)
    
    # Get views for each GIF closest to 48 hours ago
    total_views = 0
    earliest_timestamp = None
    
    for gif_id in gif_ids:
        cursor.execute('''
            SELECT view_count, recorded_at
            FROM view_history 
            WHERE gif_id = ? AND recorded_at <= ?
            ORDER BY recorded_at DESC 
            LIMIT 1
        ''', (gif_id, forty_eight_hours_ago.strftime('%Y-%m-%d %H:%M:%S')))
        
        result = cursor.fetchone()
        if result:
            total_views += result[0]
            if earliest_timestamp is None or result[1] < earliest_timestamp:
                earliest_timestamp = result[1]
    
    conn.close()
    return total_views, earliest_timestamp

def fetch_views_from_api_for_channel(channel_id, gif_ids, store_in_db=True):
    """
    Fetch current views from Giphy API for all GIFs in a channel in REAL-TIME.
    This is the only way to get views - API only provides current views, not historical.
    
    Args:
        channel_id: Channel ID
        gif_ids: List of GIF IDs to fetch views for
        store_in_db: If True, stores views in database. If False, only returns current views (real-time only).
    
    Returns:
        Dictionary with total_views, individual GIF views, and count of successfully fetched GIFs
    """
    if not GIPHY_API_KEY or GIPHY_API_KEY == 'dc6zaTOxFJmzC':
        return {'total_views': 0, 'fetched_count': 0, 'error': 'No valid API key'}
    
    total_views = 0
    fetched_count = 0
    gif_views = {}  # Store individual GIF views
    today = datetime.now().date()
    
    mode = "real-time" if not store_in_db else "with storage"
    print(f"  Fetching CURRENT views from Giphy API ({mode}) for {len(gif_ids)} GIFs...")
    
    for gif_id in gif_ids:
        try:
            # Fetch from API (REAL-TIME)
            gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
            gif_detail_params = {'api_key': GIPHY_API_KEY}
            gif_detail_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
            
            if gif_detail_response.status_code == 200:
                gif_detail = gif_detail_response.json().get('data', {})
                views = gif_detail.get('views')
                
                if views is not None:
                    try:
                        views_int = int(views)
                        if views_int >= 0:  # Allow 0 views
                            gif_views[gif_id] = views_int
                            total_views += views_int
                            fetched_count += 1
                            
                            # Only store in DB if requested
                            if store_in_db:
                                store_view_count(gif_id, views_int, recorded_date=today)
                            
                            print(f"    ✓ {gif_id[:12]}...: {views_int:,} views (from API - {mode})")
                    except (ValueError, TypeError):
                        pass
            else:
                print(f"    ✗ {gif_id[:12]}...: API returned {gif_detail_response.status_code}")
        except Exception as e:
            print(f"    ✗ {gif_id[:12]}...: Error - {str(e)[:50]}")
        
        # Small delay to avoid rate limiting
        time.sleep(0.2)
    
    print(f"  ✓ Fetched views for {fetched_count}/{len(gif_ids)} GIFs from API ({mode})")
    print(f"  ✓ Total views from API: {total_views:,}")
    
    return {
        'total_views': total_views,
        'fetched_count': fetched_count,
        'total_gifs': len(gif_ids),
        'gif_views': gif_views,  # Individual GIF views
        'success': fetched_count > 0,
        'timestamp': datetime.now().isoformat()
    }

# Lightweight cache file for real-time comparison (no database needed)
CACHE_FILE = 'channel_views_cache.json'

def get_cached_views(channel_id):
    """Get last cached views for a channel from lightweight JSON file"""
    if not os.path.exists(CACHE_FILE):
        return None
    
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            return cache.get(channel_id)
    except:
        return None

def cache_views(channel_id, views_data):
    """Cache current views for a channel in lightweight JSON file"""
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
        except:
            cache = {}
    
    cache[channel_id] = {
        'total_views': views_data['total_views'],
        'gif_views': views_data.get('gif_views', {}),
        'timestamp': views_data.get('timestamp', datetime.now().isoformat()),
        'fetched_count': views_data.get('fetched_count', 0)
    }
    
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"  Warning: Could not cache views: {e}")

def get_realtime_channel_views_comparison(channel_id, gif_ids):
    """
    Get real-time channel views from API and compare with last cached fetch.
    No database storage - uses lightweight JSON cache.
    
    Returns:
        {
            'current_views': {...},  # Current views from API
            'previous_views': {...},  # Last cached views (if available)
            'comparison': {
                'current_total': 12345,
                'previous_total': 12000,
                'difference': +345,
                'status': 'increasing' | 'decreasing' | 'stagnant' | 'no_previous'
            }
        }
    """
    # Fetch current views from API (REAL-TIME, no DB storage)
    current_data = fetch_views_from_api_for_channel(channel_id, gif_ids, store_in_db=False)
    
    # Get previous cached views
    previous_data = get_cached_views(channel_id)
    
    # Cache current views for next comparison
    cache_views(channel_id, current_data)
    
    # Compare
    comparison = {
        'current_total': current_data['total_views'],
        'previous_total': previous_data['total_views'] if previous_data else None,
        'difference': None,
        'status': 'no_previous'
    }
    
    if previous_data:
        comparison['difference'] = current_data['total_views'] - previous_data['total_views']
        if comparison['difference'] > 0:
            comparison['status'] = 'increasing'
        elif comparison['difference'] < 0:
            comparison['status'] = 'decreasing'
        else:
            comparison['status'] = 'stagnant'
    
    return {
        'current_views': current_data,
        'previous_views': previous_data,
        'comparison': comparison,
        'realtime': True
    }

def analyze_view_trends(gif_ids, days=7, channel_id=None, use_24_hour_comparison=True):
    """
    Analyze view trends by comparing total view count over multiple time periods.
    
    Three approaches:
    1. 24-hour comparison: Compares views from 24 hours ago vs now (real-time)
    2. 48-hour comparison: Compares views from 48 hours ago vs now (better for detecting slow growth)
    3. Date-based comparison: Compares views from yesterday's date vs today's date (fallback)
    
    For real-time status detection, checks both 24-hour and 48-hour trends:
    - If 24-hour shows growth → WORKING
    - If 24-hour stagnant but 48-hour shows growth → WORKING (views increasing over longer period)
    - If both stagnant/decreasing → SHADOW BANNED
    
    Args:
        gif_ids: List of GIF IDs to analyze
        days: Number of days to look back
        channel_id: Optional channel ID to fetch total views
        use_24_hour_comparison: If True, compare 24 hours ago vs now. If False, compare yesterday's date vs today's date.
    """
    total_gifs = len(gif_ids)
    if total_gifs == 0:
        return {
            'total_gifs': 0,
            'gifs_with_views': 0,
            'total_views_today': 0,
            'total_views_yesterday': 0,
            'total_views_48h_ago': 0,
            'views_difference': 0,
            'views_difference_48h': 0,
            'average_views': 0,
            'trend': 'unknown',
            'yesterday_data_available': False,
            'comparison_method': 'none'
        }
    
    total_views_today = 0
    total_views_yesterday = 0
    total_views_48h_ago = 0
    gifs_with_views = 0
    yesterday_data_available = False
    comparison_method = 'date_based'
    previous_timestamp = None
    previous_48h_timestamp = None
    
    # APPROACH 1: 24-hour and 48-hour comparison (real-time, more flexible)
    if use_24_hour_comparison and channel_id:
        total_views_yesterday, previous_timestamp = get_channel_total_views_24_hours_ago(channel_id)
        if total_views_yesterday > 0:
            yesterday_data_available = True
            comparison_method = '24_hour'
            print(f"  Using 24-hour comparison: Found views from {previous_timestamp} (24 hours ago)")
            
            # Also get 48-hour data for longer trend analysis
            total_views_48h_ago, previous_48h_timestamp = get_channel_total_views_48_hours_ago(channel_id)
            if total_views_48h_ago > 0:
                print(f"  Using 48-hour comparison: Found views from {previous_48h_timestamp} (48 hours ago)")
    
    # APPROACH 2: Date-based comparison (fallback or if 24-hour didn't work)
    if not yesterday_data_available:
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        # If channel_id is provided, try to get total views for yesterday directly
        if channel_id:
            total_views_yesterday = get_channel_total_views_for_date(channel_id, yesterday)
            if total_views_yesterday > 0:
                yesterday_data_available = True
                comparison_method = 'date_based'
                print(f"  Using date-based comparison: Found views from {yesterday}")
    
    # Get today's total views and verify yesterday's data
    for gif_id in gif_ids:
        history = get_gif_view_history(gif_id, days)
        
        if len(history) >= 1:
            # Get today's views (latest entry)
            latest = history[-1]['view_count']
            latest_date = datetime.strptime(history[-1]['date'], '%Y-%m-%d').date() if isinstance(history[-1]['date'], str) else history[-1]['date']
            
            # Add to today's total
            total_views_today += latest
            gifs_with_views += 1
            
            # If we didn't get yesterday's data from channel total, try to get it per GIF
            if not yesterday_data_available:
                # Find yesterday's view count for this specific GIF
                yesterday_views = None
                for entry in reversed(history[:-1]):  # Check from most recent to oldest (skip latest)
                    entry_date = datetime.strptime(entry['date'], '%Y-%m-%d').date() if isinstance(entry['date'], str) else entry['date']
                    if entry_date == yesterday:
                        yesterday_views = entry['view_count']
                        yesterday_data_available = True
                        break
                    elif entry_date < latest_date and entry_date >= yesterday:
                        # Use closest date to yesterday
                        yesterday_views = entry['view_count']
                        break
                
                if yesterday_views is not None:
                    # Add to yesterday's total
                    total_views_yesterday += yesterday_views
                else:
                    # No yesterday data for this GIF
                    pass
    
    # If we still don't have yesterday's data, we can't make a comparison
    # Don't use today as baseline - we need actual yesterday's data from database
    if not yesterday_data_available and total_views_yesterday == 0:
        # No historical data - we need yesterday's data to compare
        # Keep total_views_yesterday as 0 to indicate no comparison possible
        pass
    
    # Calculate differences (24h and 48h)
    views_difference = total_views_today - total_views_yesterday
    views_difference_48h = total_views_today - total_views_48h_ago if total_views_48h_ago > 0 else 0
    average_views = total_views_today / gifs_with_views if gifs_with_views > 0 else 0
    
    # Determine overall trend based on total view count comparison
    # Prioritize 48-hour trend if 24-hour is stagnant (for real-time detection)
    if not yesterday_data_available:
        trend = 'no_history'  # Need historical data
    elif total_views_today > total_views_yesterday:
        trend = 'increasing'
    elif total_views_48h_ago > 0 and total_views_today > total_views_48h_ago:
        # 24h stagnant but 48h shows growth - consider as increasing (real-time detection)
        trend = 'increasing_48h'
        print(f"  Note: 24h stagnant but 48h shows growth (+{views_difference_48h:,} views) - treating as increasing")
    elif total_views_today < total_views_yesterday:
        trend = 'decreasing'
    elif gifs_with_views == 0:
        trend = 'no_views'
    else:
        trend = 'stagnant'
    
    return {
        'total_gifs': total_gifs,
        'gifs_with_views': gifs_with_views,
        'total_views_today': total_views_today,
        'total_views_yesterday': total_views_yesterday,
        'total_views_48h_ago': total_views_48h_ago,
        'views_difference': views_difference,
        'views_difference_48h': views_difference_48h,
        'average_views': average_views,
        'trend': trend,
        'yesterday_data_available': yesterday_data_available,
        'comparison_method': comparison_method,
        'previous_timestamp': previous_timestamp,
        'previous_48h_timestamp': previous_48h_timestamp
    }

def update_gif_views_batch(gif_ids, max_workers=5):
    """
    Update view counts for a batch of GIFs by scraping Giphy pages.
    Uses threading to speed up the process.
    """
    results = []
    
    def update_single_gif(gif_id):
        try:
            views = scrape_gif_views(gif_id)
            if views is not None:
                store_view_count(gif_id, views)
                return {'gif_id': gif_id, 'views': views, 'success': True}
            else:
                return {'gif_id': gif_id, 'views': None, 'success': False}
        except Exception as e:
            return {'gif_id': gif_id, 'error': str(e), 'success': False}
    
    # Process in batches to avoid overwhelming the server
    for i in range(0, len(gif_ids), max_workers):
        batch = gif_ids[i:i + max_workers]
        threads = []
        batch_results = []
        
        def worker(gif_id, result_list):
            result = update_single_gif(gif_id)
            result_list.append(result)
        
        for gif_id in batch:
            t = threading.Thread(target=worker, args=(gif_id, batch_results))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        results.extend(batch_results)
        
        # Small delay between batches to be respectful
        if i + max_workers < len(gif_ids):
            time.sleep(1)
    
    return results

def extract_channel_info_from_url(url):
    """Extract channel username or ID from Giphy URL"""
    # Handle different Giphy URL formats
    # https://giphy.com/channel/username
    # https://giphy.com/@username
    # https://giphy.com/username (direct format)
    # https://giphy.com/channel/username/stickers
    # https://giphy.com/gifs/username-title-gifid (GIF URL format)
    
    # Clean the URL - remove protocol, www, trailing slashes
    url_original = url.strip()
    url_clean = url_original.lower().strip()
    url_clean = re.sub(r'^https?://(www\.)?', '', url_clean)
    url_clean = url_clean.rstrip('/')
    
    # Keep original for extraction to preserve case
    url = url_original.strip()
    url = re.sub(r'^https?://(www\.)?', '', url)
    url = url.rstrip('/')
    
    # Check if it's a GIF URL format: giphy.com/gifs/username-...-gifid
    gif_url_match = re.search(r'giphy\.com/gifs/([^/]+)', url_clean, re.IGNORECASE)
    if gif_url_match:
        # Extract the username from GIF URL (format: username-title-words-gifid)
        gif_path = gif_url_match.group(1)
        # The username is typically the first part before the first dash
        # But we need to be careful as titles can have dashes
        # Try to extract username - usually it's the first segment
        parts = gif_path.split('-')
        if len(parts) > 1:
            # The last part is usually the GIF ID (alphanumeric)
            # Everything before could be username-title, so try first part as username
            potential_username = parts[0]
            # Skip common words that aren't usernames
            skip_words = ['gifs', 'gif', 'stickers', 'clips']
            if potential_username.lower() not in skip_words:
                # Extract from original URL to preserve case
                orig_match = re.search(r'giphy\.com/gifs/([^/]+)', url, re.IGNORECASE)
                if orig_match:
                    orig_parts = orig_match.group(1).split('-')
                    if len(orig_parts) > 1:
                        return orig_parts[0]
                return potential_username
    
    patterns = [
        r'giphy\.com/channel/([^/?]+)',  # /channel/username
        r'giphy\.com/@([^/?]+)',          # /@username
        r'giphy\.com/([^/?]+)/channel',   # /username/channel (reverse)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            identifier = match.group(1)
            # Handle URLs with trailing paths like /stickers
            identifier = identifier.split('/')[0]
            # Remove trailing underscore if present
            return identifier.rstrip('_')
    
    # Try direct format: giphy.com/username
    # This should be the last pattern to avoid matching other paths
    direct_match = re.search(r'giphy\.com/([^/?]+)$', url, re.IGNORECASE)
    if direct_match:
        identifier = direct_match.group(1)
        # Skip common paths that aren't usernames
        skip_paths = ['explore', 'search', 'trending', 'reactions', 'artists', 'stickers', 'clips', 'upload', 'gifs']
        if identifier.lower() not in skip_paths:
            # Remove trailing underscore if present
            return identifier.rstrip('_')
    
    return None

def check_channel_via_web_scraping(channel_identifier, original_url):
    """Check channel status by scraping the Giphy webpage (no API key needed)"""
    results = {
        'channel_id': channel_identifier,
        'exists': False,
        'status': 'unknown',
        'details': {},
        'shadow_banned': False,
        'banned': False,
        'working': False,
        'error': None,
        'method': 'web_scraping'
    }
    
    try:
        # Try different URL formats
        url_formats = [
            f"https://giphy.com/{channel_identifier}",
            f"https://giphy.com/channel/{channel_identifier}",
            f"https://giphy.com/@{channel_identifier}",
            original_url
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        for url in url_formats:
            try:
                response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
                
                if response.status_code == 200:
                    html_content = response.text
                    
                    # Check if page contains channel/user information
                    # Look for JSON-LD or meta tags with user info
                    if 'giphy.com' in response.url and ('channel' in response.url or f'/{channel_identifier}' in response.url):
                        # Try to extract user data from page
                        # Giphy often embeds user data in script tags
                        script_pattern = r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>'
                        scripts = re.findall(script_pattern, html_content, re.DOTALL)
                        
                        for script in scripts:
                            try:
                                data = json.loads(script)
                                if isinstance(data, dict) and 'user' in data:
                                    user_data = data['user']
                                    results['exists'] = True
                                    results['details'] = {
                                        'username': user_data.get('username', channel_identifier),
                                        'display_name': user_data.get('display_name', ''),
                                        'user_id': user_data.get('id', ''),
                                        'profile_url': response.url,
                                    }
                                    results['working'] = True
                                    results['status'] = 'working'
                                    return results
                            except:
                                continue
                        
                        # Check for error pages or banned indicators
                        if '404' in html_content.lower() or 'not found' in html_content.lower():
                            # Before marking as not_found, check if channel appears in search results
                            # If not in search, it's BANNED (not not_found)
                            try:
                                search_visibility = check_channel_in_search_results(
                                    channel_identifier,
                                    sample_gif_ids=None,
                                    all_gifs_list=None
                                )
                                if search_visibility and not search_visibility.get('error'):
                                    visible_in_search = search_visibility.get('visible_in_search', False)
                                    if not visible_in_search:
                                        # Channel not found in search = BANNED
                                        results['exists'] = True
                                        results['status'] = 'banned'
                                        results['banned'] = True
                                        results['shadow_banned'] = False
                                        results['working'] = False
                                        results['details'] = {
                                            'username': channel_identifier,
                                            'search_visibility': search_visibility,
                                            'note': f'Channel page 404 and not found in search results - BANNED'
                                        }
                                        return results
                            except:
                                pass
                            
                            results['exists'] = False
                            results['status'] = 'not_found'
                            results['shadow_banned'] = False  # Just not found, not shadow banned
                        elif '403' in html_content.lower() or 'forbidden' in html_content.lower() or 'banned' in html_content.lower():
                            results['banned'] = True
                            results['status'] = 'banned'
                            results['working'] = False
                        else:
                            # Page exists - check if there's actual content
                            # Look for more specific indicators of working channel
                            has_gifs = 'gif' in html_content.lower() or 'sticker' in html_content.lower()
                            has_user_data = 'username' in html_content.lower() or 'user' in html_content.lower()
                            has_content = has_gifs or has_user_data
                            
                            # Check for common Giphy page elements
                            has_giphy_content = any(indicator in html_content.lower() for indicator in [
                                'giphy.com/channel', 'giphy.com/@', 'data-gif', 'data-sticker',
                                'gif-container', 'sticker-container', 'user-profile'
                            ])
                            
                            if has_content or has_giphy_content:
                                results['exists'] = True
                                results['working'] = True
                                results['status'] = 'working'
                                results['shadow_banned'] = False
                                results['details'] = {
                                    'username': channel_identifier,
                                    'profile_url': response.url,
                                }
                                
                                # Try to extract more info from the page
                                try:
                                    # Look for username in meta tags or JSON
                                    username_match = re.search(r'"username"\s*:\s*"([^"]+)"', html_content)
                                    if username_match:
                                        results['details']['username'] = username_match.group(1)
                                except:
                                    pass
                                
                                return results
                            else:
                                # Page exists but no clear content - might be shadow banned
                                results['exists'] = True
                                results['shadow_banned'] = True
                                results['status'] = 'shadow_banned'
                                results['working'] = False
                                results['details'] = {
                                    'username': channel_identifier,
                                    'profile_url': response.url,
                                }
                        
                        return results
                        
                elif response.status_code == 403:
                    results['banned'] = True
                    results['status'] = 'banned'
                    results['working'] = False
                    return results
                elif response.status_code == 404:
                    continue  # Try next URL format
                    
            except requests.exceptions.RequestException:
                continue
        
        # If all URLs failed, check search visibility before marking as not_found
        # If channel name search returns no GIFs, it's BANNED (not not_found)
        try:
            search_visibility = check_channel_in_search_results(
                channel_identifier,
                sample_gif_ids=None,
                all_gifs_list=None
            )
            if search_visibility and not search_visibility.get('error'):
                visible_in_search = search_visibility.get('visible_in_search', False)
                if not visible_in_search:
                    # Channel name not found in search results = BANNED
                    results['exists'] = True  # Channel exists (we searched for it), just banned
                    results['status'] = 'banned'
                    results['banned'] = True
                    results['shadow_banned'] = False
                    results['working'] = False
                    results['details'] = {
                        'username': channel_identifier,
                        'search_visibility': search_visibility,
                        'note': f'Channel "{channel_identifier}" not found in search results - no GIFs/views found = BANNED'
                    }
                    return results
        except Exception as e:
            # Search check failed, continue to mark as not_found
            pass
        
        # If search check passed or failed, mark as not_found
        results['exists'] = False
        results['status'] = 'not_found'
        results['shadow_banned'] = False  # Don't assume shadow ban if we can't access
        
    except Exception as e:
        results['error'] = f"Web scraping error: {str(e)}"
        results['status'] = 'error'
    
    return results

def extract_tags_from_gif_urls(all_gifs_list, max_tags=10):
    """
    Extract tags from GIF URLs.
    Giphy URL format: giphy.com/gifs/username-tag1-tag2-tag3-gifid
    
    Returns a list of unique tags found in URLs.
    """
    tags_set = set()
    import re
    
    # Common stop words to filter out
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those', 'gif', 'gifs'}
    
    # Extract tags from URLs
    for gif in all_gifs_list[:50]:  # Check first 50 GIFs to get more tags
        url = gif.get('url', '')
        if url:
            # Extract slug from URL: giphy.com/gifs/username-tag1-tag2-tag3-gifid
            # Pattern: match everything between username (after /gifs/) and GIF ID (last alphanumeric segment)
            url_match = re.search(r'giphy\.com/gifs/([^/]+)$', url)
            if url_match:
                full_slug = url_match.group(1)
                # Split by dashes
                parts = full_slug.split('-')
                
                # Skip first part (usually username) and last part (GIF ID)
                # Everything in between are tags
                if len(parts) > 2:
                    # Tags are everything except first (username) and last (gifid)
                    tags = parts[1:-1]
                    for tag in tags:
                        tag_clean = tag.lower().strip()
                        # Filter out stop words and short words
                        if tag_clean and tag_clean not in stop_words and len(tag_clean) >= 2:
                            tags_set.add(tag_clean)
    
    # Convert to list and limit
    tags_list = list(tags_set)[:max_tags]
    return tags_list

def extract_keywords_from_gifs(all_gifs_list, max_keywords=5):
    """
    Extract keywords from GIF titles and URLs for search testing.
    
    Returns a list of unique keywords to test in search.
    """
    keywords_set = set()
    import re
    
    # Common stop words to filter out
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those'}
    
    # Extract from titles
    for gif in all_gifs_list[:20]:  # Check first 20 GIFs
        title = gif.get('title', '')
        if title:
            # Extract words from title (remove special chars, split by spaces)
            words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
            for word in words:
                if word not in stop_words and len(word) >= 3:
                    keywords_set.add(word)
        
        # Extract from URL slug (if available)
        url = gif.get('url', '')
        if url:
            # Extract slug from URL: giphy.com/gifs/channel-keyword1-keyword2-gifid
            url_match = re.search(r'giphy\.com/gifs/[^/]+-([^-]+(?:-[^-]+)*?)-[a-zA-Z0-9]+$', url)
            if url_match:
                slug = url_match.group(1)
                # Split slug by dashes
                slug_words = slug.split('-')
                for word in slug_words:
                    word_clean = word.lower().strip()
                    if word_clean and word_clean not in stop_words and len(word_clean) >= 3:
                        keywords_set.add(word_clean)
    
    # Convert to list and limit
    keywords_list = list(keywords_set)[:max_keywords]
    return keywords_list

def check_tags_in_search_results(tags_list, channel_id, sample_gif_ids=None):
    """
    Check if GIF tags from channel appear in Giphy search results.
    
    Logic:
    - Extract tags from GIF URLs
    - Search Giphy for each tag
    - Count how many tags return channel GIFs in results
    - If 5+ tags found → WORKING
    - If tags not found → SHADOW BANNED
    
    Returns:
        {
            'tags_tested': int,
            'tags_found': int,
            'tags_visible': list,
            'tags_not_visible': list,
            'is_working': bool  # True if 5+ tags found
        }
    """
    try:
        if not GIPHY_API_KEY or GIPHY_API_KEY == 'dc6zaTOxFJmzC':
            return None
        
        if not tags_list or len(tags_list) == 0:
            return {'tags_tested': 0, 'tags_found': 0, 'tags_visible': [], 'tags_not_visible': [], 'is_working': False}
        
        search_url = f"{GIPHY_API_BASE}/gifs/search"
        channel_id_lower = channel_id.lower()
        tags_visible = []
        tags_not_visible = []
        
        # Test up to 10 tags
        tags_to_test = tags_list[:10]
        print(f"  Testing {len(tags_to_test)} tags from GIF URLs...")
        
        for tag in tags_to_test:
            try:
                search_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': tag.strip(),
                    'limit': 25
                }
                
                response = requests.get(search_url, params=search_params, timeout=10)
                
                if response.status_code == 200:
                    search_results = response.json().get('data', [])
                    
                    # Check if any GIFs from this channel appear in search results
                    found_channel_gif = False
                    for gif in search_results:
                        gif_user = gif.get('user')
                        if gif_user:
                            gif_username = gif_user.get('username', '').lower()
                            if gif_username == channel_id_lower:
                                found_channel_gif = True
                                break
                        
                        # Also check by GIF ID if provided
                        if sample_gif_ids and gif.get('id') in sample_gif_ids:
                            found_channel_gif = True
                            break
                    
                    if found_channel_gif:
                        tags_visible.append(tag)
                        print(f"    ✓ Tag '{tag}': Found channel GIFs in search")
                    else:
                        tags_not_visible.append(tag)
                        print(f"    ✗ Tag '{tag}': No channel GIFs in search")
                
                time.sleep(0.3)  # Small delay to avoid rate limiting
                
            except Exception as e:
                print(f"    ⚠️  Error testing tag '{tag}': {str(e)[:50]}")
                tags_not_visible.append(tag)
                continue
        
        tags_found = len(tags_visible)
        is_working = tags_found >= 5  # 5+ tags found = WORKING
        
        print(f"  Tag visibility: {tags_found}/{len(tags_to_test)} tags found channel GIFs in search")
        
        return {
            'tags_tested': len(tags_to_test),
            'tags_found': tags_found,
            'tags_visible': tags_visible,
            'tags_not_visible': tags_not_visible,
            'is_working': is_working
        }
    except Exception as e:
        return {'error': str(e)}
    
    return None

def check_channel_in_search_results(channel_id, sample_gif_ids=None, all_gifs_list=None):
    """
    PRIMARY CHECK: Check if channel appears in general search results using multiple methods.
    This is a strong indicator of channel status.
    
    Logic:
    1. Search for channel name directly
    2. Extract keywords from GIF titles/URLs
    3. Search for those keywords
    4. Check if any GIFs from this channel appear in ANY search results
    - If GIFs appear → WORKING (channel is visible in search)
    - If no GIFs appear → SHADOW BANNED (channel suppressed from search)
    
    Args:
        channel_id: Channel username/ID
        sample_gif_ids: List of known GIF IDs from this channel (for verification)
        all_gifs_list: List of all GIFs from the channel (for keyword extraction)
    
    Returns:
        {
            'visible_in_search': True/False,
            'matching_gifs_count': int,
            'search_queries_tested': list,
            'successful_queries': list,
            'status': 'working' | 'shadow_banned' | 'unknown'
        }
    """
    try:
        if not GIPHY_API_KEY or GIPHY_API_KEY == 'dc6zaTOxFJmzC':
            return None
        
        search_url = f"{GIPHY_API_BASE}/gifs/search"
        search_queries_tested = []
        successful_queries = []
        total_matching_gifs = 0
        found_gif_ids = set()
        channel_id_lower = channel_id.lower()
        
        # Build list of search queries to test
        queries_to_test = [channel_id]  # Always test channel name first
        
        # Extract keywords from GIFs if available
        if all_gifs_list and len(all_gifs_list) > 0:
            keywords = extract_keywords_from_gifs(all_gifs_list, max_keywords=5)
            queries_to_test.extend(keywords)
            print(f"  Extracted {len(keywords)} keywords from GIF titles/URLs: {keywords[:3]}...")
        
        # Limit total queries to test (to avoid too many API calls)
        queries_to_test = queries_to_test[:6]  # Max 6 queries: channel name + 5 keywords
        
        print(f"  Testing {len(queries_to_test)} search queries...")
        
        # Test each query
        for query in queries_to_test:
            if not query or len(query.strip()) < 2:
                continue
            
            try:
                search_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': query.strip(),
                    'limit': 25
                }
                
                search_queries_tested.append(query)
                response = requests.get(search_url, params=search_params, timeout=10)
                
                if response.status_code == 200:
                    search_results = response.json().get('data', [])
                    
                    # Check if any GIFs from this channel appear in search results
                    query_matching_gifs = 0
                    query_matched_gif_ids = set()  # Track GIFs already counted for this query
                    
                    for gif in search_results:
                        gif_id = gif.get('id')
                        is_match = False
                        
                        # Method 1: Check by username in user object
                        gif_user = gif.get('user')
                        if gif_user:
                            gif_username = gif_user.get('username', '').lower()
                            if gif_username == channel_id_lower:
                                is_match = True
                        
                        # Method 2: Verify using known GIF IDs (if provided)
                        if not is_match and sample_gif_ids and gif_id and gif_id in sample_gif_ids:
                            is_match = True
                        
                        # Count this GIF only once
                        if is_match and gif_id and gif_id not in query_matched_gif_ids:
                            query_matching_gifs += 1
                            total_matching_gifs += 1
                            query_matched_gif_ids.add(gif_id)
                            found_gif_ids.add(gif_id)
                    
                    if query_matching_gifs > 0:
                        successful_queries.append({
                            'query': query,
                            'matching_gifs': query_matching_gifs
                        })
                        print(f"    ✓ '{query}': Found {query_matching_gifs} matching GIFs")
                    else:
                        print(f"    ✗ '{query}': No matching GIFs")
                
                # Small delay to avoid rate limiting
                time.sleep(0.3)
                
            except Exception as e:
                print(f"    ⚠️  Error testing query '{query}': {str(e)[:50]}")
                continue
        
        # Determine visibility based on results
        visible = total_matching_gifs > 0 or len(successful_queries) > 0
        
        print(f"  Results: {len(successful_queries)}/{len(search_queries_tested)} queries found channel GIFs")
        
        return {
            'visible_in_search': visible,
            'matching_gifs_count': total_matching_gifs,
            'search_queries_tested': search_queries_tested,
            'successful_queries': successful_queries,
            'total_search_queries': len(search_queries_tested),
            'successful_count': len(successful_queries),
            'status': 'working' if visible else 'shadow_banned',
            'found_gif_ids': list(found_gif_ids) if found_gif_ids else []
        }
    except Exception as e:
        return {'error': str(e)}
    
    return None

def analyze_channel_status(user_data, all_gifs_list, user_id, gifs_endpoint_404=False, channel_id=None, auto_check_views=False, gifs_accessible_via_detail=None):
    """
    Analyze channel status using multiple indicators (Search Results + View Trends).
    
    DETECTION LOGIC (Priority Order):
    ===================================================
    
    1. BANNED 🚫
       - Channel not found in search results
       - Content is NOT visible (can't find any GIFs)
    
    2. PRIMARY CHECK: Search Result Visibility
       - Search for channel name/keywords in Giphy search
       - If channel GIFs appear in search results → WORKING ✅
       - If channel GIFs DON'T appear in search → SHADOW BANNED 👻
    
    3. SECONDARY CHECK: View Trends (if search check is inconclusive)
       - Views increasing in K-M range → WORKING ✅
       - Views increasing by very little (15-20) → SHADOW BANNED 👻
       - Views stagnant/decreasing → SHADOW BANNED 👻
    
    4. FALLBACK: Alternative Methods (if view data unavailable)
       - Recent upload activity
       - Trending status
       - Search visibility test
    
    Args:
        user_data: User data from API
        all_gifs_list: List of all GIFs/stickers found
        user_id: User ID (if available)
        gifs_endpoint_404: Whether /users/{user_id}/gifs endpoint returned 404
        channel_id: Channel identifier for database lookup
        auto_check_views: If True, automatically scrape views if not in database
        gifs_accessible_via_detail: Number of GIFs accessible via detail endpoint (indicator of working channel)
    
    Returns:
        Dictionary with status analysis (shadow_banned, banned, working, status, analysis_reasons, view_trends)
    """
    analysis = {
        'shadow_banned': False,
        'banned': False,
        'working': False,
        'status': 'unknown',
        'analysis_reasons': [],
        'view_trends': None
    }
    
    total_uploads = len(all_gifs_list) if all_gifs_list else 0
    gifs_count = len([g for g in all_gifs_list if not g.get('is_sticker')]) if all_gifs_list else 0
    
    print(f"\n{'='*50}")
    print(f"ANALYZING CHANNEL STATUS (Search Results + View Trends)")
    print(f"{'='*50}")
    print(f"Total uploads: {total_uploads} ({gifs_count} GIFs)")
    print(f"User ID available: {user_id is not None}")
    print(f"GIFs endpoint 404: {gifs_endpoint_404}")
    
    # Factor 1: BANNED - Channel not found, content not visible, NO VIEWS
    # BANNED = Channel shows nothing, no views, no content accessible
    if not user_data or total_uploads == 0:
        analysis['banned'] = True
        analysis['working'] = False
        analysis['shadow_banned'] = False
        analysis['status'] = 'banned'
        analysis['analysis_reasons'].append('🚫 BANNED: Channel not found or content not visible in API - no views, no content accessible')
        print("  🚫 BANNED: Channel/content not visible - no views, no content")
        return analysis
    
    # Get GIF IDs for analysis
    gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')]
    
    if not gif_ids:
        analysis['status'] = 'unknown'
        analysis['analysis_reasons'].append('No GIF IDs available for analysis')
        return analysis
    
    # PRIMARY CHECK: Search Result Visibility
    # Check if channel GIFs appear in general search results (we'll combine with view trends)
    print(f"\n{'='*50}")
    print(f"CHECK 1: Search Result Visibility")
    print(f"{'='*50}")
    
    search_visibility = None
    visible_in_search = False
    if channel_id:
        try:
            print(f"  Checking if channel '{channel_id}' appears in Giphy search results...")
            print(f"  Testing channel name + keywords extracted from GIF titles/URLs...")
            search_visibility = check_channel_in_search_results(
                channel_id, 
                sample_gif_ids=gif_ids[:10] if gif_ids else None,
                all_gifs_list=all_gifs_list
            )
            
            if search_visibility and not search_visibility.get('error'):
                visible_in_search = search_visibility.get('visible_in_search', False)
                matching_count = search_visibility.get('matching_gifs_count', 0)
                successful_queries = search_visibility.get('successful_queries', [])
                queries_tested = search_visibility.get('search_queries_tested', [])
                
                if visible_in_search:
                    successful_query_list = [q['query'] for q in successful_queries]
                    queries_str = ', '.join(successful_query_list[:3])
                    if len(successful_query_list) > 3:
                        queries_str += f' (+{len(successful_query_list) - 3} more)'
                    print(f"  ✅ SEARCH RESULT: VISIBLE")
                    print(f"     Found {matching_count} matching GIFs across {len(successful_queries)} successful search queries")
                    print(f"     Successful queries: {queries_str}")
                    
                    # If visible in search, also check tags from URLs
                    if all_gifs_list and len(all_gifs_list) > 0:
                        print(f"\n  Checking tags from GIF URLs...")
                        tags_list = extract_tags_from_gif_urls(all_gifs_list, max_tags=10)
                        if tags_list and len(tags_list) > 0:
                            print(f"  Extracted {len(tags_list)} tags from GIF URLs: {tags_list[:5]}...")
                            tags_result = check_tags_in_search_results(tags_list, channel_id, sample_gif_ids=gif_ids[:10] if gif_ids else None)
                            if tags_result and not tags_result.get('error'):
                                tags_found = tags_result.get('tags_found', 0)
                                tags_tested = tags_result.get('tags_tested', 0)
                                search_visibility['tags_check'] = tags_result
                                print(f"  Tags check result: {tags_found}/{tags_tested} tags found channel GIFs in search")
                        else:
                            print(f"  No tags extracted from GIF URLs")
                else:
                    queries_str = ', '.join(queries_tested[:5])
                    if len(queries_tested) > 5:
                        queries_str += f' (+{len(queries_tested) - 5} more)'
                    print(f"  👻 SEARCH RESULT: NOT VISIBLE")
                    print(f"     Tested {len(queries_tested)} search queries, no matching GIFs found")
                
                analysis['search_visibility'] = search_visibility
            else:
                error_msg = search_visibility.get('error', 'Unknown error') if search_visibility else 'No result'
                print(f"  ⚠️  Search visibility check failed: {error_msg}")
        except Exception as e:
            print(f"  ⚠️  Search visibility check error: {str(e)}")
    
    print(f"\n{'='*50}")
    print(f"CHECK 2: View Trends Analysis")
    print(f"{'='*50}")
    
    # Check for view trends in database (LAST 2 DAYS)
    view_trend_analysis = None
    if channel_id:
        try:
            # First check if we have any view history
            has_history = False
            for gif_id in gif_ids[:5]:  # Check first 5 GIFs
                history = get_gif_view_history(gif_id, days=2)
                if history and len(history) > 0:
                    has_history = True
                    break
            
            # Check if we have yesterday's views in database
            yesterday = (datetime.now() - timedelta(days=1)).date()
            yesterday_total_views = get_channel_total_views_for_date(channel_id, yesterday)
            has_yesterday_data = yesterday_total_views > 0
            
            # If no history and auto_check_views is enabled, try real-time comparison first
            if not has_history and auto_check_views:
                print(f"  No database history found. Trying real-time comparison...")
                
                # Try real-time cache comparison first (no database storage)
                try:
                    realtime_comparison = get_realtime_channel_views_comparison(channel_id, gif_ids)
                    
                    if realtime_comparison['comparison']['status'] != 'no_previous':
                        # Real-time comparison worked - use it
                        print(f"  ✓ Real-time comparison available (using cache, no database storage)")
                        # Skip database storage and use real-time data
                        has_history = True  # Mark as having data for analysis
                    else:
                        # First time - no previous cache, fetch current views
                        print(f"  First time checking - fetching current views from Giphy API...")
                        print(f"  Note: Giphy API only provides CURRENT views, not historical data.")
                        
                        # Fetch current views (will be cached for next comparison)
                        api_result = fetch_views_from_api_for_channel(channel_id, gif_ids, store_in_db=False)
                        
                        # If API didn't work or returned no views, fall back to scraping
                        if not api_result['success'] or api_result['fetched_count'] == 0:
                            print(f"  API didn't return views, falling back to web scraping...")
                            # Scrape views for all GIFs and cache them
                            gif_url_map = {gif.get('id'): gif.get('url') for gif in all_gifs_list if gif.get('id')}
                            gif_views_data = {'total_views': 0, 'gif_views': {}, 'fetched_count': 0, 'timestamp': datetime.now().isoformat()}
                            
                            for gif_id in gif_ids:
                                try:
                                    gif_url = gif_url_map.get(gif_id)  # Get URL from API response
                                    views = scrape_gif_views_with_proxy(gif_id, proxy=None, location='default', gif_url=gif_url)
                                    if views is not None:
                                        gif_views_data['gif_views'][gif_id] = views
                                        gif_views_data['total_views'] += views
                                        gif_views_data['fetched_count'] += 1
                                        print(f"    Scraped {gif_id[:12]}...: {views:,} views")
                                except Exception as e:
                                    print(f"    Error scraping {gif_id}: {str(e)}")
                                time.sleep(0.5)  # Small delay
                            
                            # Cache the scraped views
                            if gif_views_data['fetched_count'] > 0:
                                cache_views(channel_id, gif_views_data)
                                print(f"  ✓ Cached {gif_views_data['fetched_count']} GIF views for next comparison")
                except Exception as e:
                    print(f"  ⚠️  Real-time comparison failed: {str(e)}")
                    print(f"  Falling back to database storage method...")
                    
                    # Fallback: Store in database
                    api_result = fetch_views_from_api_for_channel(channel_id, gif_ids, store_in_db=True)
                    
                    if not api_result['success'] or api_result['fetched_count'] == 0:
                        print(f"  API didn't return views, falling back to web scraping...")
                        gif_url_map = {gif.get('id'): gif.get('url') for gif in all_gifs_list if gif.get('id')}
                        
                        for gif_id in gif_ids:
                            try:
                                gif_url = gif_url_map.get(gif_id)
                                views = scrape_gif_views_with_proxy(gif_id, proxy=None, location='default', gif_url=gif_url)
                                if views is not None:
                                    store_view_count(gif_id, views)
                                    print(f"    Scraped {gif_id[:12]}...: {views:,} views")
                            except Exception as e:
                                print(f"    Error scraping {gif_id}: {str(e)}")
                            time.sleep(0.5)
            
            # Now analyze view trends (Today vs Yesterday)
            view_trend_analysis = analyze_view_trends(gif_ids, days=2, channel_id=channel_id)
            analysis['view_trends'] = view_trend_analysis
            
            # If no database history, try real-time cache comparison
            yesterday_data_available = view_trend_analysis.get('yesterday_data_available', False)
            if not yesterday_data_available and auto_check_views:
                print(f"  No database history found. Trying real-time cache comparison...")
                try:
                    realtime_comparison = get_realtime_channel_views_comparison(channel_id, gif_ids)
                    
                    if realtime_comparison['comparison']['status'] != 'no_previous':
                        # Use real-time comparison results - OVERRIDE database analysis
                        current_total = realtime_comparison['comparison']['current_total']
                        previous_total = realtime_comparison['comparison']['previous_total']
                        difference = realtime_comparison['comparison']['difference']
                        status = realtime_comparison['comparison']['status']
                        
                        # Update view_trend_analysis with real-time data
                        view_trend_analysis['total_views_today'] = current_total
                        view_trend_analysis['total_views_yesterday'] = previous_total
                        view_trend_analysis['views_difference'] = difference
                        view_trend_analysis['yesterday_data_available'] = True
                        view_trend_analysis['comparison_method'] = 'realtime_cache'
                        view_trend_analysis['gifs_with_views'] = realtime_comparison['current_views'].get('fetched_count', len(gif_ids))
                        
                        if status == 'increasing':
                            view_trend_analysis['trend'] = 'increasing'
                        elif status == 'decreasing':
                            view_trend_analysis['trend'] = 'decreasing'
                        else:
                            view_trend_analysis['trend'] = 'stagnant'
                        
                        # Update average views
                        if view_trend_analysis['gifs_with_views'] > 0:
                            view_trend_analysis['average_views'] = current_total / view_trend_analysis['gifs_with_views']
                        
                        print(f"  ✓ Using real-time cache comparison (no database storage)")
                        print(f"    Current: {current_total:,} | Previous: {previous_total:,} | Status: {status}")
                    else:
                        print(f"  ⚠️  First time checking - no previous data in cache. Will compare on next check.")
                        # Update with current views from real-time fetch
                        current_total = realtime_comparison['current_views'].get('total_views', 0)
                        view_trend_analysis['total_views_today'] = current_total
                        view_trend_analysis['gifs_with_views'] = realtime_comparison['current_views'].get('fetched_count', 0)
                        if view_trend_analysis['gifs_with_views'] > 0:
                            view_trend_analysis['average_views'] = current_total / view_trend_analysis['gifs_with_views']
                except Exception as e:
                    print(f"  ⚠️  Real-time comparison failed: {str(e)}")
                    import traceback
                    traceback.print_exc()
            
            print(f"View Trends Analysis (Real-time - 24h and 48h comparison):")
            print(f"  Total GIFs: {view_trend_analysis['total_gifs']}")
            print(f"  GIFs with views: {view_trend_analysis['gifs_with_views']}")
            print(f"  Total views today: {view_trend_analysis['total_views_today']:,}")
            print(f"  Total views 24h ago: {view_trend_analysis['total_views_yesterday']:,}")
            if view_trend_analysis.get('total_views_48h_ago', 0) > 0:
                print(f"  Total views 48h ago: {view_trend_analysis['total_views_48h_ago']:,}")
            print(f"  Views difference (24h): {view_trend_analysis['views_difference']:+,}")
            if view_trend_analysis.get('views_difference_48h', 0) != 0:
                print(f"  Views difference (48h): {view_trend_analysis['views_difference_48h']:+,}")
            print(f"  Overall trend: {view_trend_analysis['trend']}")
            if view_trend_analysis['gifs_with_views'] > 0:
                print(f"  Average views: {view_trend_analysis['average_views']:,.0f}")
        except Exception as e:
            print(f"Error analyzing view trends: {str(e)}")
            view_trend_analysis = None
    
    # ANALYSIS BASED ON VIEW TRENDS (Today vs Yesterday) - SIMPLE LOGIC:
    
    # Factor 2: Use view trends as PRIMARY indicator
    # Simple: Compare total view count today vs yesterday
    # If total_views_today > total_views_yesterday → WORKING
    # If total_views_today <= total_views_yesterday → SHADOW BANNED
    if view_trend_analysis:
        trend = view_trend_analysis['trend']
        total_gifs = view_trend_analysis['total_gifs']
        gifs_with_views = view_trend_analysis['gifs_with_views']
        total_views_today = view_trend_analysis['total_views_today']
        total_views_yesterday = view_trend_analysis['total_views_yesterday']
        views_difference = view_trend_analysis['views_difference']
        
        # Print view count comparison
        yesterday_data_available = view_trend_analysis.get('yesterday_data_available', False)
        comparison_method = view_trend_analysis.get('comparison_method', 'date_based')
        previous_timestamp = view_trend_analysis.get('previous_timestamp')
        
        # View comparison display (24h and 48h)
        print(f"  View Comparison (Real-time):")
        print(f"    Current views: {total_views_today:,}")
        if yesterday_data_available:
            if comparison_method == '24_hour':
                print(f"    Previous views (24h ago): {total_views_yesterday:,}")
            else:
                print(f"    Previous views (yesterday): {total_views_yesterday:,}")
            print(f"    Difference (24h): {views_difference:+,} views")
            
            # Show 48h comparison if available
            total_views_48h_ago = view_trend_analysis.get('total_views_48h_ago', 0)
            views_difference_48h = view_trend_analysis.get('views_difference_48h', 0)
            if total_views_48h_ago > 0:
                print(f"    Previous views (48h ago): {total_views_48h_ago:,}")
                print(f"    Difference (48h): {views_difference_48h:+,} views")
        else:
            print(f"    Previous views: Not available")
            print(f"    ⚠️  Need previous data to compare")
        
        # DECISION LOGIC: 
        # - BANNED: Channel not found in search results (handled earlier)
        # - WORKING: Views increasing in K-M range (thousands to millions)
        # - SHADOW BANNED: Views increasing by very little (15-20 count) OR views not increasing
        # CRITICAL: If gifs_with_views == 0, we have NO view data = cannot verify views are increasing = shadow banned
        if gifs_with_views == 0:
            # NO VIEWS TRACKED = Cannot verify views are increasing
            # Shadow banned = views NOT increasing. If we can't verify, assume shadow banned.
            scraping_attempted = channel_id and auto_check_views
            
            if scraping_attempted and user_id and gifs_endpoint_404:
                # Endpoint 404 + scraping attempted but no views = shadow banned
                analysis['shadow_banned'] = True
                analysis['working'] = False
                analysis['banned'] = False
                analysis['status'] = 'shadow_banned'
                analysis['analysis_reasons'].append(f'Channel has {total_uploads} uploads but NO views tracked. Endpoint 404 + view scraping failed - CANNOT VERIFY views are increasing. Shadow banned = views NOT increasing - SHADOW BANNED')
                print(f"  👻 SHADOW BANNED: No views tracked - cannot verify views are increasing (shadow banned = views NOT increasing)")
            else:
                # No views but context unclear - still shadow banned
                analysis['shadow_banned'] = True
                analysis['working'] = False
                analysis['banned'] = False
                analysis['status'] = 'shadow_banned'
                analysis['analysis_reasons'].append(f'Channel has {total_uploads} uploads but NO views tracked. Cannot verify views are increasing - SHADOW BANNED (shadow banned = views NOT increasing)')
                print(f"  👻 SHADOW BANNED: No views tracked - cannot verify views are increasing")
        elif gifs_with_views > 0:
            # VIEW-BASED LOGIC: Compare total view counts and check magnitude of increase
            # - WORKING: Views increasing in K-M range (thousands to millions)
            # - SHADOW BANNED: Views increasing by very little (15-20 count) OR views not increasing
            
            # First check if we have previous data to compare
            if not yesterday_data_available:
                # No previous data - cannot determine status yet
                analysis['status'] = 'unknown'
                analysis['working'] = False
                analysis['shadow_banned'] = False
                analysis['banned'] = False
                analysis['analysis_reasons'].append(f'Current views: {total_views_today:,} | Previous views: Not available | Status: Cannot determine (need previous data)')
                print(f"  ⚠️  STATUS: UNKNOWN")
                print(f"     Current views: {total_views_today:,}")
                print(f"     Previous views: Not available")
                print(f"     Action: Run check again tomorrow to compare")
            elif total_views_today > total_views_yesterday or trend == 'increasing_48h':
                # Views are increasing (24h or 48h) - check magnitude to determine if WORKING or SHADOW BANNED
                # SHADOW BANNED: Views increasing by very little (15-20 count per day)
                # WORKING: Views increasing in K-M range (thousands to millions per day)
                
                # Use 48h trend if 24h is stagnant but 48h shows growth (real-time detection)
                use_48h_trend = (trend == 'increasing_48h' and total_views_today == total_views_yesterday)
                if use_48h_trend:
                    # Use 48-hour comparison for real-time detection
                    total_views_48h_ago = view_trend_analysis.get('total_views_48h_ago', 0)
                    views_difference_48h = view_trend_analysis.get('views_difference_48h', 0)
                    base_views = total_views_48h_ago
                    absolute_increase = views_difference_48h
                    time_period = "48h"
                    print(f"    Using 48h trend for real-time detection (24h stagnant, 48h shows growth)")
                else:
                    # Use 24-hour comparison
                    base_views = total_views_yesterday
                    absolute_increase = views_difference
                    time_period = "24h"
                
                # Calculate percentage increase
                percentage_increase = (absolute_increase / base_views * 100) if base_views > 0 else 0
                
                # Threshold for shadow ban: very small increase (15-50 views total, or very low percentage)
                # Threshold for working: significant increase (1000+ views or 1%+ increase on large channels)
                # For 48h trends, adjust thresholds (divide by 2 since it's 2x the time period)
                if use_48h_trend:
                    SHADOW_BAN_THRESHOLD = 100  # 48h: 50 views/day * 2 days = 100 views
                    WORKING_THRESHOLD = 2000    # 48h: 1000 views/day * 2 days = 2000 views
                else:
                    SHADOW_BAN_THRESHOLD = 50   # 24h: If increase is less than 50 views, likely shadow banned
                    WORKING_THRESHOLD = 1000    # 24h: If increase is 1000+ views, likely working
                
                # For channels with very high views, use percentage-based threshold
                # If total views is high (millions), even small percentage can mean thousands of views
                if base_views >= 100000:  # 100K+ views
                    # Use percentage threshold for large channels (0.1% = significant)
                    if percentage_increase >= 0.1 or absolute_increase >= WORKING_THRESHOLD:
                        # WORKING: Significant increase (K-M range)
                        prev_views_display = base_views
                        change_display = absolute_increase
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        if use_48h_trend:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Views increased over 48h from {prev_views_display:,} to {total_views_today:,} (+{change_display:,} views, {percentage_increase:+.2f}%) - significant increase in K-M range (real-time detection)')
                            print(f"  ✅ STATUS: WORKING (Real-time - 48h trend)")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (48h ago): {prev_views_display:,}")
                            print(f"     Change (48h): +{change_display:,} views ({percentage_increase:+.2f}%) - SIGNIFICANT INCREASE (K-M range)")
                        else:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Views increased from {prev_views_display:,} to {total_views_today:,} (+{change_display:,} views, {percentage_increase:+.2f}%) - significant increase in K-M range')
                            print(f"  ✅ STATUS: WORKING")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {prev_views_display:,}")
                            print(f"     Change (24h): +{change_display:,} views ({percentage_increase:+.2f}%) - SIGNIFICANT INCREASE (K-M range)")
                    elif absolute_increase <= SHADOW_BAN_THRESHOLD:
                        # SHADOW BANNED: Very small increase (15-20 count range)
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by only {views_difference:,} views ({percentage_increase:+.2f}%) from {total_views_yesterday:,} to {total_views_today:,} - very small increase (15-20 count range)')
                        print(f"  👻 STATUS: SHADOW BANNED")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views: {total_views_yesterday:,}")
                        print(f"     Change: +{views_difference:,} views ({percentage_increase:+.2f}%) - VERY SMALL INCREASE (15-20 count range)")
                    else:
                        # Medium increase (50-1000 views) - could be either, but conservative = shadow banned
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by {views_difference:,} views ({percentage_increase:+.2f}%) from {total_views_yesterday:,} to {total_views_today:,} - moderate increase but not in K-M range')
                        print(f"  👻 STATUS: SHADOW BANNED")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views: {total_views_yesterday:,}")
                        print(f"     Change: +{views_difference:,} views ({percentage_increase:+.2f}%) - MODERATE INCREASE (not in K-M range)")
                else:
                    # For smaller channels, use absolute threshold
                    prev_views_display = base_views
                    change_display = absolute_increase
                    if absolute_increase >= WORKING_THRESHOLD:
                        # Views are increasing significantly - check search visibility too
                        views_increasing_significantly = True
                        # Will combine with search visibility below
                        if use_48h_trend:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Views increased over 48h from {prev_views_display:,} to {total_views_today:,} (+{change_display:,} views, {percentage_increase:+.2f}%) - significant increase in K-M range (real-time detection)')
                            print(f"  ✅ STATUS: WORKING (Real-time - 48h trend)")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (48h ago): {prev_views_display:,}")
                            print(f"     Change (48h): +{change_display:,} views ({percentage_increase:+.2f}%) - SIGNIFICANT INCREASE (K-M range)")
                        else:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Views increased from {prev_views_display:,} to {total_views_today:,} (+{change_display:,} views, {percentage_increase:+.2f}%) - significant increase in K-M range')
                            print(f"  ✅ STATUS: WORKING")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {prev_views_display:,}")
                            print(f"     Change (24h): +{change_display:,} views ({percentage_increase:+.2f}%) - SIGNIFICANT INCREASE (K-M range)")
                    elif absolute_increase <= SHADOW_BAN_THRESHOLD:
                        # SHADOW BANNED: Very small increase (15-20 count range)
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by only {views_difference:,} views ({percentage_increase:+.2f}%) from {total_views_yesterday:,} to {total_views_today:,} - very small increase (15-20 count range)')
                        print(f"  👻 STATUS: SHADOW BANNED")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views: {total_views_yesterday:,}")
                        print(f"     Change: +{views_difference:,} views ({percentage_increase:+.2f}%) - VERY SMALL INCREASE (15-20 count range)")
                    else:
                        # Medium increase (50-1000 views) - conservative = shadow banned if not clearly working
                        if percentage_increase >= 5.0:  # 5%+ increase is significant for smaller channels
                            views_increasing_significantly = True
                            # Will combine with search visibility below
                            analysis['analysis_reasons'].append(f'Views increased from {total_views_yesterday:,} to {total_views_today:,} (+{views_difference:,} views, {percentage_increase:+.2f}%) - significant percentage increase')
                        else:
                            analysis['shadow_banned'] = True
                            analysis['working'] = False
                            analysis['banned'] = False
                            analysis['status'] = 'shadow_banned'
                            analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by {views_difference:,} views ({percentage_increase:+.2f}%) from {total_views_yesterday:,} to {total_views_today:,} - moderate increase but not in K-M range')
                            print(f"  👻 STATUS: SHADOW BANNED")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views: {total_views_yesterday:,}")
                            print(f"     Change: +{views_difference:,} views ({percentage_increase:+.2f}%) - MODERATE INCREASE (not in K-M range)")
            else:
                # Check if 48h trend shows growth (real-time detection for slow-growing channels)
                total_views_48h_ago = view_trend_analysis.get('total_views_48h_ago', 0)
                views_difference_48h = view_trend_analysis.get('views_difference_48h', 0)
                
                if total_views_48h_ago > 0 and total_views_today > total_views_48h_ago and views_difference_48h > 0:
                    # 48h shows growth - treat as WORKING even if 24h is stagnant/decreasing (real-time detection)
                    percentage_increase_48h = (views_difference_48h / total_views_48h_ago * 100) if total_views_48h_ago > 0 else 0
                    WORKING_THRESHOLD_48H = 2000  # 48h threshold
                    SHADOW_BAN_THRESHOLD_48H = 100  # 48h shadow ban threshold
                    
                    if views_difference_48h >= WORKING_THRESHOLD_48H or (total_views_48h_ago >= 100000 and percentage_increase_48h >= 0.1):
                        # WORKING: 48h shows significant growth
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'✅ WORKING: Views increased over 48h from {total_views_48h_ago:,} to {total_views_today:,} (+{views_difference_48h:,} views, {percentage_increase_48h:+.2f}%) - significant increase detected via 48h trend (real-time)')
                        print(f"  ✅ STATUS: WORKING (Real-time - 48h trend shows growth)")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                        print(f"     Change (48h): +{views_difference_48h:,} views ({percentage_increase_48h:+.2f}%) - SIGNIFICANT INCREASE (K-M range)")
                        print(f"     Note: 24h comparison shows {views_difference:+,} views, but 48h trend indicates growth")
                    elif views_difference_48h <= SHADOW_BAN_THRESHOLD_48H:
                        # SHADOW BANNED: 48h shows very small growth
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by only {views_difference_48h:,} views over 48h ({percentage_increase_48h:+.2f}%) - very small increase (15-20 count range)')
                        print(f"  👻 STATUS: SHADOW BANNED")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                        print(f"     Change (48h): +{views_difference_48h:,} views ({percentage_increase_48h:+.2f}%) - VERY SMALL INCREASE")
                    else:
                        # Medium 48h growth - conservative = shadow banned
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by {views_difference_48h:,} views over 48h ({percentage_increase_48h:+.2f}%) - moderate increase but not in K-M range')
                        print(f"  👻 STATUS: SHADOW BANNED")
                        print(f"     Current views: {total_views_today:,}")
                        print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                        print(f"     Change (48h): +{views_difference_48h:,} views ({percentage_increase_48h:+.2f}%) - MODERATE INCREASE (not in K-M range)")
                else:
                    # Check if this is a very large channel (10M+ views) - be more lenient
                    # Large channels with millions of views are clearly working, even if views appear stagnant
                    VERY_LARGE_CHANNEL_THRESHOLD = 10000000  # 10M+ views
                    
                    if total_views_today >= VERY_LARGE_CHANNEL_THRESHOLD:
                        # Very large channel - if accessible, assume WORKING (millions of views = clearly working channel)
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        if total_views_today == total_views_yesterday:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Very large channel ({total_views_today:,} views) - views appear stagnant over short period but channel has millions of views (clearly working)')
                            print(f"  ✅ STATUS: WORKING")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                            print(f"     Change (24h): {views_difference:,} views")
                            print(f"     Note: Very large channel (10M+ views) - clearly working even if views appear stagnant")
                            if total_views_48h_ago > 0:
                                print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                                print(f"     Change (48h): {views_difference_48h:,} views")
                        else:
                            analysis['analysis_reasons'].append(f'✅ WORKING: Very large channel ({total_views_today:,} views) - slight decrease over short period but channel has millions of views (clearly working)')
                            print(f"  ✅ STATUS: WORKING")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                            print(f"     Change (24h): {views_difference:,} views")
                            print(f"     Note: Very large channel (10M+ views) - clearly working despite slight variation")
                            if total_views_48h_ago > 0:
                                print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                                print(f"     Change (48h): {views_difference_48h:,} views")
                    else:
                        # Check if views are STAGNANT (not increasing) vs DECREASING
                        # SHADOW BANNED: Views STAGNANT (no change or very small increase 15-20)
                        # WORKING: Views decreasing is normal fluctuation - still WORKING
                        if total_views_today == total_views_yesterday:
                            # STAGNANT: No change in views = SHADOW BANNED
                            analysis['shadow_banned'] = True
                            analysis['working'] = False
                            analysis['banned'] = False
                            analysis['status'] = 'shadow_banned'
                            analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views stagnant at {total_views_today:,} (not increasing over 24h or 48h)')
                            print(f"  👻 STATUS: SHADOW BANNED")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                            print(f"     Change (24h): {views_difference:,} views (STAGNANT - not increasing)")
                            if total_views_48h_ago > 0:
                                print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                                print(f"     Change (48h): {views_difference_48h:,} views")
                        elif views_difference < 0:
                            # DECREASING: Views decreased - this is normal fluctuation, still WORKING
                            # Don't mark as shadow banned just because views decreased
                            analysis['working'] = True
                            analysis['status'] = 'working'
                            analysis['shadow_banned'] = False
                            analysis['banned'] = False
                            analysis['analysis_reasons'].append(f'✅ WORKING: Views decreased from {total_views_yesterday:,} to {total_views_today:,} ({views_difference:,} views) - normal fluctuation, channel still working')
                            print(f"  ✅ STATUS: WORKING")
                            print(f"     Current views: {total_views_today:,}")
                            print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                            print(f"     Change (24h): {views_difference:,} views (DECREASING - normal fluctuation)")
                            print(f"     Note: Decreasing views is normal - channel is still working")
                            if total_views_48h_ago > 0:
                                print(f"     Previous views (48h ago): {total_views_48h_ago:,}")
                                print(f"     Change (48h): {views_difference_48h:,} views")
                        else:
                            # Small positive increase but not significant - check if it's in shadow ban range (15-20)
                            if views_difference <= 50:  # Very small increase (15-50 views) = shadow banned
                                analysis['shadow_banned'] = True
                                analysis['working'] = False
                                analysis['banned'] = False
                                analysis['status'] = 'shadow_banned'
                                analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Views increased by only {views_difference:,} views from {total_views_yesterday:,} to {total_views_today:,} - very small increase (15-20 count range)')
                                print(f"  👻 STATUS: SHADOW BANNED")
                                print(f"     Current views: {total_views_today:,}")
                                print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                                print(f"     Change (24h): +{views_difference:,} views (VERY SMALL INCREASE - shadow banned range)")
                            else:
                                # Moderate increase - still working
                                analysis['working'] = True
                                analysis['status'] = 'working'
                                analysis['shadow_banned'] = False
                                analysis['banned'] = False
                                analysis['analysis_reasons'].append(f'✅ WORKING: Views increased from {total_views_yesterday:,} to {total_views_today:,} (+{views_difference:,} views) - channel working')
                                print(f"  ✅ STATUS: WORKING")
                                print(f"     Current views: {total_views_today:,}")
                                print(f"     Previous views (24h ago): {total_views_yesterday:,}")
                                print(f"     Change (24h): +{views_difference:,} views")
            
            # Legacy check for no views (shouldn't happen if we have gifs_with_views > 0)
            no_views_percent = ((total_gifs - gifs_with_views) / total_gifs) * 100 if total_gifs > 0 else 0
            if no_views_percent >= 70:
                # SHADOW BANNED: 70%+ have no views
                analysis['shadow_banned'] = True
                analysis['working'] = False
                analysis['banned'] = False
                analysis['status'] = 'shadow_banned'
                analysis['analysis_reasons'].append(f'{total_gifs - gifs_with_views}/{total_gifs} GIFs ({no_views_percent:.1f}%) have NO views over last 2 days - SHADOW BANNED')
                print(f"  👻 SHADOW BANNED: {no_views_percent:.1f}% of GIFs have no views")
        else:
            # No views at all - Check accessibility and upload count before deciding
            # If GIFs are accessible and channel has many uploads, likely working even if views can't be tracked
            
            # Check if we attempted to scrape but failed
            scraping_attempted = channel_id and auto_check_views
            
            # Check accessibility indicators
            accessible_gifs_count = gifs_accessible_via_detail if gifs_accessible_via_detail is not None else 0
            accessibility_ratio = (accessible_gifs_count / total_uploads) if total_uploads > 0 else 0
            MANY_UPLOADS_THRESHOLD = 50  # Channels with 50+ uploads are likely working
            GOOD_ACCESSIBILITY_THRESHOLD = 0.5  # 50%+ accessible = good sign
            
            # Decision logic: If channel has many uploads AND GIFs are accessible, likely WORKING
            if total_uploads >= MANY_UPLOADS_THRESHOLD and accessible_gifs_count > 0:
                # Channel has many uploads and GIFs are accessible - likely WORKING
                analysis['working'] = True
                analysis['status'] = 'working'
                analysis['shadow_banned'] = False
                analysis['banned'] = False
                if scraping_attempted:
                    analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {total_uploads} uploads with {accessible_gifs_count} GIFs accessible via detail endpoint ({accessibility_ratio*100:.1f}%). View scraping failed but channel appears active - WORKING')
                    print(f"  ✅ STATUS: WORKING")
                    print(f"     Channel has {total_uploads} uploads with {accessible_gifs_count} accessible GIFs ({accessibility_ratio*100:.1f}%)")
                    print(f"     View scraping failed but channel appears active (many uploads + accessible GIFs)")
                else:
                    analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {total_uploads} uploads with {accessible_gifs_count} GIFs accessible ({accessibility_ratio*100:.1f}%) - channel appears active')
                    print(f"  ✅ STATUS: WORKING")
                    print(f"     Channel has {total_uploads} uploads with {accessible_gifs_count} accessible GIFs ({accessibility_ratio*100:.1f}%)")
            elif accessible_gifs_count > 0 and accessibility_ratio >= GOOD_ACCESSIBILITY_THRESHOLD:
                # Good accessibility ratio (50%+) - likely WORKING
                analysis['working'] = True
                analysis['status'] = 'working'
                analysis['shadow_banned'] = False
                analysis['banned'] = False
                analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {accessible_gifs_count}/{total_uploads} GIFs accessible ({accessibility_ratio*100:.1f}%) - good accessibility indicates channel is working')
                print(f"  ✅ STATUS: WORKING")
                print(f"     {accessible_gifs_count}/{total_uploads} GIFs accessible ({accessibility_ratio*100:.1f}%) - good accessibility")
            elif scraping_attempted:
                # Scraping attempted but failed - check context
                if user_id and gifs_endpoint_404:
                    # Endpoint 404 + no views + low accessibility = shadow banned
                    if accessible_gifs_count == 0 or accessibility_ratio < 0.3:
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['banned'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Channel has {total_uploads} uploads but only {accessible_gifs_count} GIFs accessible ({accessibility_ratio*100:.1f}%). User endpoint 404 and view scraping failed - SHADOW BANNED')
                        print(f"  👻 SHADOW BANNED: Endpoint 404 + low accessibility ({accessibility_ratio*100:.1f}%) + view scraping failed")
                    else:
                        # Some accessibility - mark as unknown
                        analysis['status'] = 'unknown'
                        analysis['working'] = False
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Channel has {total_uploads} uploads with {accessible_gifs_count} GIFs accessible ({accessibility_ratio*100:.1f}%). Endpoint 404 and view scraping failed - cannot determine status')
                        print(f"  ⚠️  UNKNOWN: Endpoint 404 + some accessibility ({accessibility_ratio*100:.1f}%) + view scraping failed")
                else:
                    # Endpoint works but views can't be scraped - mark as unknown
                    analysis['status'] = 'unknown'
                    analysis['working'] = False
                    analysis['shadow_banned'] = False
                    analysis['banned'] = False
                    analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Channel accessible but view scraping failed. Cannot determine if views are increasing - need view data for accurate status')
                    print(f"  ⚠️  UNKNOWN: View scraping failed - cannot verify views are increasing")
            else:
                # No view data yet (not attempted) - need data collection
                # But if channel has many uploads and GIFs are accessible, likely working
                if total_uploads >= MANY_UPLOADS_THRESHOLD and accessible_gifs_count > 0:
                    analysis['working'] = True
                    analysis['status'] = 'working'
                    analysis['shadow_banned'] = False
                    analysis['banned'] = False
                    analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {total_uploads} uploads with {accessible_gifs_count} accessible GIFs ({accessibility_ratio*100:.1f}%) - appears active (view tracking not yet started)')
                    print(f"  ✅ STATUS: WORKING")
                    print(f"     Channel has {total_uploads} uploads with {accessible_gifs_count} accessible GIFs ({accessibility_ratio*100:.1f}%)")
                    print(f"     View tracking not yet started, but channel appears active")
                else:
                    # No view data - try alternative detection methods
                    print(f"  ⚠️  No view data available - trying alternative detection methods...")
                    
                    # Use alternative methods as fallback
                    gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')]
                    alternative_analysis = None
                    if ALTERNATIVE_METHODS_AVAILABLE:
                        try:
                            alternative_analysis = alternative_detection_methods.comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids)
                        except Exception as e:
                            print(f"  ⚠️  Alternative methods error: {str(e)}")
                            alternative_analysis = None
                    
                    if alternative_analysis and alternative_analysis.get('alternative_status') != 'unknown':
                        alt_status = alternative_analysis.get('alternative_status')
                        composite_score = alternative_analysis.get('composite_score', 0)
                        
                        analysis['alternative_methods'] = alternative_analysis
                        
                        if alt_status == 'working' and composite_score >= 50:
                            analysis['working'] = True
                            analysis['status'] = 'working'
                            analysis['shadow_banned'] = False
                            analysis['banned'] = False
                            
                            reasons = []
                            if alternative_analysis.get('recent_activity', {}).get('activity_status') == 'active':
                                reasons.append(f"Recent upload activity detected")
                            if alternative_analysis.get('trending_status', {}).get('has_trending_gifs'):
                                reasons.append(f"Has trending GIFs")
                            if alternative_analysis.get('general_search', {}).get('visibility_rate', 0) >= 40:
                                reasons.append(f"Good search visibility ({alternative_analysis.get('general_search', {}).get('visibility_rate', 0):.1f}%)")
                            
                            analysis['analysis_reasons'].append(f'✅ WORKING: Alternative methods indicate working channel (score: {composite_score}/100). ' + ', '.join(reasons))
                            print(f"  ✅ STATUS: WORKING (Alternative methods - score: {composite_score}/100)")
                            print(f"     Recent activity: {alternative_analysis.get('recent_activity', {}).get('activity_status', 'unknown')}")
                            print(f"     Trending GIFs: {alternative_analysis.get('trending_status', {}).get('has_trending_gifs', False)}")
                            print(f"     Search visibility: {alternative_analysis.get('general_search', {}).get('visibility_rate', 0):.1f}%")
                        elif alt_status == 'shadow_banned' and composite_score <= 0:
                            analysis['shadow_banned'] = True
                            analysis['working'] = False
                            analysis['status'] = 'shadow_banned'
                            analysis['banned'] = False
                            analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Alternative methods indicate shadow banned (score: {composite_score}/100)')
                            print(f"  👻 STATUS: SHADOW BANNED (Alternative methods - score: {composite_score}/100)")
                        else:
                            analysis['status'] = 'unknown'
                            analysis['working'] = False
                            analysis['shadow_banned'] = False
                            analysis['banned'] = False
                            analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100). Need view data for accurate status')
                            print(f"  ⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)")
                    else:
                        analysis['status'] = 'unknown'
                        analysis['working'] = False
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Channel accessible but no view data collected yet. Need to collect views over 2 days to verify if views are increasing')
                        print(f"  ⚠️  UNKNOWN: No view data - need 2 days of tracking to verify views are increasing")
    else:
        # No view trend data available - cannot determine accurately
        # Check if we attempted scraping but failed
        scraping_failed = False
        if channel_id and total_uploads > 0 and auto_check_views:
            # If auto_check_views was enabled but we still have no views,
            # scraping likely failed
            scraping_failed = True
        
        if total_uploads > 0:
            if user_id and gifs_endpoint_404:
                # Endpoint 404 could indicate shadow ban, but check other indicators
                # Check if GIFs are accessible via detail endpoint (better indicator)
                accessible_ratio = 0
                if gifs_accessible_via_detail is not None:
                    accessible_ratio = (gifs_accessible_via_detail / total_uploads) if total_uploads > 0 else 0
                    print(f"  GIF accessibility check: {gifs_accessible_via_detail}/{total_uploads} GIFs accessible via detail endpoint ({accessible_ratio*100:.1f}%)")
                
                # Decision logic when endpoint 404 but we have other indicators
                if gifs_accessible_via_detail is not None and gifs_accessible_via_detail > 0:
                    # GIFs ARE accessible via detail endpoint - channel is likely WORKING
                    # Endpoint 404 might be normal (some channels don't have that endpoint working)
                    # CRITICAL: Shadow banned = views NOT increasing
                    # If scraping failed, we CANNOT verify views are increasing → assume shadow banned
                    # Accessibility alone doesn't prove views are increasing!
                    
                    # Check if channel has many uploads - if so, likely working even if scraping failed
                    MANY_UPLOADS_THRESHOLD = 50  # Channels with 50+ uploads are likely working
                    if total_uploads >= MANY_UPLOADS_THRESHOLD and accessible_gifs_count > 0:
                        # Many uploads + accessible GIFs = WORKING (even if scraping failed)
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {total_uploads} uploads with {accessible_gifs_count} GIFs accessible ({accessible_ratio*100:.1f}%). Endpoint 404 and view scraping failed, but channel appears active - WORKING')
                        print(f"  ✅ WORKING: {total_uploads} uploads + {accessible_gifs_count} accessible GIFs ({accessible_ratio*100:.1f}%) - channel appears active")
                    elif accessible_ratio >= 0.5:  # 50%+ accessible = WORKING
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'Channel has {gifs_accessible_via_detail}/{total_uploads} GIFs accessible ({accessible_ratio*100:.1f}%). User endpoint 404 but content accessible - WORKING (need view data for confirmation)')
                        print(f"  ✅ WORKING: {accessible_ratio*100:.1f}% of GIFs accessible - need view data to confirm")
                    elif accessible_ratio >= 0.3:  # 30-50% accessible = uncertain
                        analysis['status'] = 'unknown'
                        analysis['working'] = False
                        analysis['shadow_banned'] = False
                        analysis['analysis_reasons'].append(f'Channel has {gifs_accessible_via_detail}/{total_uploads} GIFs accessible ({accessible_ratio*100:.1f}%). Mixed signals - need view data for accurate status')
                        print(f"  ⚠️  UNKNOWN: {accessible_ratio*100:.1f}% accessible - mixed signals")
                    else:  # <30% accessible = likely shadow banned
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['analysis_reasons'].append(f'Channel has only {gifs_accessible_via_detail}/{total_uploads} GIFs accessible ({accessible_ratio*100:.1f}%). User endpoint 404 and most GIFs not accessible - SHADOW BANNED')
                        print(f"  👻 SHADOW BANNED: Only {accessible_ratio*100:.1f}% accessible")
                else:
                    # No accessibility data - check upload count
                    MANY_UPLOADS_THRESHOLD = 50  # Channels with 50+ uploads are likely working
                    if total_uploads >= MANY_UPLOADS_THRESHOLD:
                        # Many uploads but no accessibility data - likely working
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'✅ WORKING: Channel has {total_uploads} uploads. Endpoint 404 but channel appears active - WORKING')
                        print(f"  ✅ WORKING: {total_uploads} uploads - channel appears active")
                    elif scraping_failed:
                        # Try alternative methods before marking as shadow banned
                        print(f"  ⚠️  View scraping failed - trying alternative detection methods...")
                        gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')] if all_gifs_list else []
                        alternative_analysis = None
                        if ALTERNATIVE_METHODS_AVAILABLE:
                            try:
                                alternative_analysis = alternative_detection_methods.comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids)
                            except Exception as e:
                                print(f"  ⚠️  Alternative methods error: {str(e)}")
                                alternative_analysis = None
                        
                        if alternative_analysis and alternative_analysis.get('alternative_status') == 'working' and alternative_analysis.get('composite_score', 0) >= 50:
                            # Alternative methods indicate working
                            analysis['working'] = True
                            analysis['status'] = 'working'
                            analysis['shadow_banned'] = False
                            analysis['banned'] = False
                            analysis['alternative_methods'] = alternative_analysis
                            analysis['analysis_reasons'].append(f'✅ WORKING: Alternative methods indicate working channel (score: {alternative_analysis.get("composite_score", 0)}/100) despite endpoint 404')
                            print(f"  ✅ STATUS: WORKING (Alternative methods - score: {alternative_analysis.get('composite_score', 0)}/100)")
                        else:
                            # Few uploads + no accessibility data + scraping failed = shadow banned
                            analysis['shadow_banned'] = True
                            analysis['working'] = False
                            analysis['banned'] = False
                            analysis['status'] = 'shadow_banned'
                            analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Channel visible with {total_uploads} uploads but user endpoint 404. View scraping failed and no accessibility data - SHADOW BANNED')
                            print(f"  👻 SHADOW BANNED: Endpoint 404 + no accessibility data + view scraping failed")
                    else:
                        # No view data yet - try alternative methods
                        print(f"  ⚠️  No view data - trying alternative detection methods...")
                        gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')] if all_gifs_list else []
                        alternative_analysis = None
                        if ALTERNATIVE_METHODS_AVAILABLE:
                            try:
                                alternative_analysis = alternative_detection_methods.comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids)
                            except Exception as e:
                                print(f"  ⚠️  Alternative methods error: {str(e)}")
                                alternative_analysis = None
                        
                        if alternative_analysis and alternative_analysis.get('alternative_status') != 'unknown':
                            alt_status = alternative_analysis.get('alternative_status')
                            composite_score = alternative_analysis.get('composite_score', 0)
                            analysis['alternative_methods'] = alternative_analysis
                            
                            if alt_status == 'working' and composite_score >= 50:
                                analysis['working'] = True
                                analysis['status'] = 'working'
                                analysis['shadow_banned'] = False
                                analysis['banned'] = False
                                analysis['analysis_reasons'].append(f'✅ WORKING: Alternative methods indicate working channel (score: {composite_score}/100)')
                                print(f"  ✅ STATUS: WORKING (Alternative methods - score: {composite_score}/100)")
                            elif alt_status == 'shadow_banned':
                                analysis['shadow_banned'] = True
                                analysis['working'] = False
                                analysis['status'] = 'shadow_banned'
                                analysis['banned'] = False
                                analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Alternative methods indicate shadow banned (score: {composite_score}/100)')
                                print(f"  👻 STATUS: SHADOW BANNED (Alternative methods - score: {composite_score}/100)")
                            else:
                                analysis['status'] = 'unknown'
                                analysis['working'] = False
                                analysis['shadow_banned'] = False
                                analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)')
                                print(f"  ⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)")
                        else:
                            analysis['status'] = 'unknown'
                            analysis['working'] = False
                            analysis['shadow_banned'] = False
                            analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Channel visible but user endpoint 404. Need view data to verify if views are increasing')
                            print(f"  ⚠️  UNKNOWN: Endpoint 404 + no view data - need view tracking to verify")
            elif scraping_failed:
                # Scraping failed - try alternative methods
                print(f"  ⚠️  View scraping failed - trying alternative detection methods...")
                gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')] if all_gifs_list else []
                alternative_analysis = None
                if ALTERNATIVE_METHODS_AVAILABLE:
                    try:
                        alternative_analysis = alternative_detection_methods.comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids)
                    except Exception as e:
                        print(f"  ⚠️  Alternative methods error: {str(e)}")
                        alternative_analysis = None
                
                if alternative_analysis and alternative_analysis.get('alternative_status') != 'unknown':
                    alt_status = alternative_analysis.get('alternative_status')
                    composite_score = alternative_analysis.get('composite_score', 0)
                    analysis['alternative_methods'] = alternative_analysis
                    
                    if alt_status == 'working' and composite_score >= 50:
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'✅ WORKING: Alternative methods indicate working channel (score: {composite_score}/100)')
                        print(f"  ✅ STATUS: WORKING (Alternative methods - score: {composite_score}/100)")
                    elif alt_status == 'shadow_banned':
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Alternative methods indicate shadow banned (score: {composite_score}/100)')
                        print(f"  👻 STATUS: SHADOW BANNED (Alternative methods - score: {composite_score}/100)")
                    else:
                        analysis['status'] = 'unknown'
                        analysis['working'] = False
                        analysis['shadow_banned'] = False
                        analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)')
                        print(f"  ⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)")
                else:
                    analysis['status'] = 'unknown'
                    analysis['working'] = False
                    analysis['shadow_banned'] = False
                    analysis['analysis_reasons'].append(f'Channel accessible with {total_uploads} uploads, but view scraping failed. Cannot determine status without view data.')
                    print(f"  ⚠️  UNKNOWN: View scraping failed - cannot determine status")
            else:
                # No view data yet, but haven't tried scraping - try alternative methods
                print(f"  ⚠️  No view data - trying alternative detection methods...")
                gif_ids = [gif.get('id') for gif in all_gifs_list if gif.get('id')] if all_gifs_list else []
                alternative_analysis = None
                if ALTERNATIVE_METHODS_AVAILABLE:
                    try:
                        alternative_analysis = alternative_detection_methods.comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids)
                    except Exception as e:
                        print(f"  ⚠️  Alternative methods error: {str(e)}")
                        alternative_analysis = None
                
                if alternative_analysis and alternative_analysis.get('alternative_status') != 'unknown':
                    alt_status = alternative_analysis.get('alternative_status')
                    composite_score = alternative_analysis.get('composite_score', 0)
                    analysis['alternative_methods'] = alternative_analysis
                    
                    if alt_status == 'working' and composite_score >= 50:
                        analysis['working'] = True
                        analysis['status'] = 'working'
                        analysis['shadow_banned'] = False
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'✅ WORKING: Alternative methods indicate working channel (score: {composite_score}/100)')
                        print(f"  ✅ STATUS: WORKING (Alternative methods - score: {composite_score}/100)")
                    elif alt_status == 'shadow_banned':
                        analysis['shadow_banned'] = True
                        analysis['working'] = False
                        analysis['status'] = 'shadow_banned'
                        analysis['banned'] = False
                        analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Alternative methods indicate shadow banned (score: {composite_score}/100)')
                        print(f"  👻 STATUS: SHADOW BANNED (Alternative methods - score: {composite_score}/100)")
                    else:
                        analysis['status'] = 'unknown'
                        analysis['working'] = False
                        analysis['shadow_banned'] = False
                        analysis['analysis_reasons'].append(f'⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)')
                        print(f"  ⚠️  UNKNOWN: Alternative methods inconclusive (score: {composite_score}/100)")
                else:
                    analysis['status'] = 'unknown'
                    analysis['working'] = False
                    analysis['shadow_banned'] = False
                    analysis['analysis_reasons'].append(f'Channel accessible but no view trend data. Need to collect views over 2 days for accurate analysis.')
                    print(f"  ⚠️  UNKNOWN: No view data - need 2 days of view tracking")
    
    # Final determination
    print(f"\nAnalysis Result:")
    print(f"  Status: {analysis['status']}")
    print(f"  Shadow Banned: {analysis['shadow_banned']}")
    # FINAL COMBINED DECISION: Prioritize Search Visibility
    # WORKING = Visible in search results (regardless of view trends) OR (5+ tags found in search)
    # SHADOW BANNED = Not visible in search AND (views stagnant OR tags not found)
    # Priority: Search visibility is the strongest indicator - if visible, channel is WORKING
    if not analysis.get('banned') and search_visibility is not None and visible_in_search is not None:
        # Get view trend data if available
        yesterday_data_available = False
        views_difference = 0
        total_views_yesterday = 0
        views_increasing = False
        views_stagnant = False
        
        if view_trend_analysis:
            yesterday_data_available = view_trend_analysis.get('yesterday_data_available', False)
            views_difference = view_trend_analysis.get('views_difference', 0)
            total_views_yesterday = view_trend_analysis.get('total_views_yesterday', 0)
            
            # Check if views are increasing significantly OR if views are stagnant
            # SHADOW BANNED = STAGNANT (no change or very small increase 15-20)
            # WORKING = Increasing significantly OR decreasing (normal fluctuation)
            if yesterday_data_available:
                if views_difference == 0:
                    # STAGNANT: No change = shadow banned
                    views_stagnant = True
                elif views_difference > 0:
                    # POSITIVE: Check if significant increase
                    if views_difference >= 1000:  # 1000+ views increase = significant
                        views_increasing = True
                    elif total_views_yesterday > 0:
                        percentage = (views_difference / total_views_yesterday) * 100
                        if percentage >= 0.1:  # 0.1%+ increase for large channels = significant
                            views_increasing = True
                    elif views_difference <= 50:  # Very small increase (15-50) = stagnant/shadow banned
                        views_stagnant = True
                # Note: views_difference < 0 (decreasing) is treated as WORKING (normal fluctuation)
            if views_difference == 0:
                # STAGNANT: No change = shadow banned
                views_stagnant = True
            elif views_difference > 0:
                # POSITIVE: Check if significant increase
                if views_difference >= 1000:  # 1000+ views increase = significant
                    views_increasing = True
                elif total_views_yesterday > 0:
                    percentage = (views_difference / total_views_yesterday) * 100
                    if percentage >= 0.1:  # 0.1%+ increase for large channels = significant
                        views_increasing = True
                elif views_difference <= 50:  # Very small increase (15-50) = stagnant/shadow banned
                    views_stagnant = True
            # Note: views_difference < 0 (decreasing) is treated as WORKING (normal fluctuation)
        
        # Final decision based on BOTH factors
        print(f"\n{'='*50}")
        print(f"FINAL COMBINED DECISION (Search Visibility + View Trends)")
        print(f"{'='*50}")
        print(f"  Search Visibility: {'✅ Visible' if visible_in_search else '❌ Not Visible'}")
        if yesterday_data_available:
            if views_stagnant:
                trend_text = f'❌ Stagnant ({views_difference:+,} views)'
            elif views_difference < 0:
                trend_text = f'📉 Decreasing ({views_difference:+,} views) - Normal fluctuation'
            elif views_increasing:
                trend_text = f'✅ Increasing ({views_difference:+,} views)'
            else:
                trend_text = f'⚠️  Small increase ({views_difference:+,} views)'
            print(f"  View Trend: {trend_text}")
        else:
            print(f"  View Trend: ⚠️  No previous data available")
        
        # Check tags visibility if available
        tags_check = search_visibility.get('tags_check') if search_visibility else None
        tags_working = False
        if tags_check and not tags_check.get('error'):
            tags_found = tags_check.get('tags_found', 0)
            tags_working = tags_check.get('is_working', False)  # True if 5+ tags found
            if tags_working:
                print(f"  Tags Visibility: ✅ {tags_found} tags found in search (5+ tags = WORKING)")
        
        # WORKING if: Visible in search OR (5+ tags found in search)
        if visible_in_search or tags_working:
            # WORKING: Channel visible in search results (regardless of view trends)
            analysis['working'] = True
            analysis['status'] = 'working'
            analysis['shadow_banned'] = False
            analysis['banned'] = False
            
            reason_parts = []
            if visible_in_search:
                reason_parts.append('visible in search results')
            if tags_working:
                reason_parts.append(f'{tags_check.get("tags_found", 0)} tags found in search')
            if yesterday_data_available:
                if views_difference < 0:
                    reason_parts.append(f'views decreased ({views_difference:,} views) - normal fluctuation')
                elif views_increasing:
                    reason_parts.append(f'views increasing (+{views_difference:,} views)')
            
            reason_str = ' AND '.join(reason_parts)
            analysis['analysis_reasons'].append(f'✅ WORKING: Channel {reason_str}')
            print(f"  ✅ FINAL STATUS: WORKING (Visible in search{' + Tags visible' if tags_working else ''})")
        elif not visible_in_search or (yesterday_data_available and views_stagnant):
            # SHADOW BANNED: Views stagnant (but visible in search - this shouldn't happen due to earlier check, but keep as fallback)
            analysis['shadow_banned'] = True
            analysis['working'] = False
            analysis['banned'] = False
            analysis['status'] = 'shadow_banned'
            reasons = [f'views stagnant (no increase, {views_difference:+,} views)']
            if tags_check and tags_check.get('tags_found', 0) < 5:
                reasons.append(f'only {tags_check.get("tags_found", 0)} tags found in search (need 5+)')
            reason_str = ' and '.join(reasons)
            analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Channel {reason_str}')
            print(f"  👻 FINAL STATUS: SHADOW BANNED ({reason_str})")
        else:
            # No previous view data - use search visibility only
            if visible_in_search:
                analysis['working'] = True
                analysis['status'] = 'working'
                analysis['shadow_banned'] = False
                analysis['analysis_reasons'].append(f'✅ WORKING: Channel visible in search results (view trend data not yet available)')
                print(f"  ✅ FINAL STATUS: WORKING (Visible in search, view trend pending)")
            else:
                analysis['shadow_banned'] = True
                analysis['working'] = False
                analysis['status'] = 'shadow_banned'
                analysis['analysis_reasons'].append(f'👻 SHADOW BANNED: Channel not visible in search results')
                print(f"  👻 FINAL STATUS: SHADOW BANNED (Not visible in search)")
    
    print(f"  Banned: {analysis['banned']}")
    print(f"  Working: {analysis['working']}")
    print(f"  Reasons: {', '.join(analysis['analysis_reasons'])}")
    print(f"{'='*50}\n")
    
    return analysis

def check_channel_status(channel_identifier, original_url=None):
    """
    Check Giphy channel status using Giphy API with the provided API key.
    All data is fetched from the API based on the channel URL.
    """
    results = {
        'channel_id': channel_identifier,
        'exists': False,
        'status': 'unknown',
        'details': {},
        'shadow_banned': False,
        'banned': False,
        'working': False,
        'error': None,
        'method': 'api',
        'api_key_used': GIPHY_API_KEY[:10] + '...' if GIPHY_API_KEY else 'none'  # Show partial key for verification
    }
    
    # PRIMARY METHOD: Use Giphy API with the provided API key
    # All data should come from the API based on the channel URL extracted from the input
    # The channel_identifier is extracted from the URL and used to search/fetch data via API
    
    if not GIPHY_API_KEY or GIPHY_API_KEY == 'dc6zaTOxFJmzC':
        results['error'] = 'Invalid or missing Giphy API key. Please set GIPHY_API_KEY environment variable.'
        results['status'] = 'error'
        return results
    
    print(f"\n{'='*50}")
    print(f"Searching for channel: {channel_identifier}")
    print(f"Using API Key: {GIPHY_API_KEY[:10]}...")
    print(f"{'='*50}\n")
    
    try:
        # Step 1: Search for the user/channel using multiple methods
        # Method 1: User search API
        # Method 2: Search GIFs by username to extract user info
        # Method 3: Try variations of the identifier
        
        user_data = None
        response = None
        search_lower = channel_identifier.lower()
        
        # Method 1: Search GIFs by username parameter (PRIMARY METHOD)
        # NOTE: /users/search endpoint doesn't exist (returns 404), so we skip it
        # and use GIF search by username which is the reliable method
        print("Using GIF search by username (primary method)")
        print(f"Searching for: {channel_identifier}\n")
        
        # Method 1: Search GIFs by username parameter (PRIMARY METHOD)
        method1_gifs = []  # Initialize variable to store GIFs found
        if not user_data:
            try:
                print(f"Method 1: Search GIFs AND Stickers by username (fetching ALL uploads)")
                
                # Fetch ALL GIFs and Stickers with pagination
                all_search_gifs = []
                limit = 50  # Maximum per request
                max_pages = 10  # Fetch up to 500 items
                
                print(f"  Username: {channel_identifier}")
                
                # First, fetch GIFs
                print(f"  Fetching GIFs...")
                gifs_search_url = f"{GIPHY_API_BASE}/gifs/search"
                offset = 0
                
                for page in range(max_pages):
                    gifs_search_params = {
                        'api_key': GIPHY_API_KEY,
                        'q': '',  # Empty query
                        'username': channel_identifier,
                        'limit': limit,
                        'offset': offset
                    }
                    
                    gifs_search_response = requests.get(gifs_search_url, params=gifs_search_params, timeout=10)
                    
                    if gifs_search_response.status_code == 200:
                        gifs_data = gifs_search_response.json()
                        gifs_list = gifs_data.get('data', [])
                        pagination = gifs_data.get('pagination', {})
                        
                        if len(gifs_list) > 0:
                            all_search_gifs.extend(gifs_list)
                            total_count = pagination.get('total_count', 0)
                            current_total = len(all_search_gifs)
                            
                            print(f"    GIFs Page {page + 1}: {len(gifs_list)} found (Total: {current_total}, API total: {total_count})")
                            
                            if total_count > 0 and current_total >= total_count:
                                break
                            if len(gifs_list) < limit:
                                break
                            
                            offset += len(gifs_list)
                        else:
                            break
                    else:
                        break
                
                # Now fetch Stickers separately
                print(f"  Fetching Stickers...")
                stickers_search_url = f"{GIPHY_API_BASE}/stickers/search"
                stickers_offset = 0
                
                for page in range(max_pages):
                    stickers_search_params = {
                        'api_key': GIPHY_API_KEY,
                        'q': '',  # Empty query
                        'username': channel_identifier,
                        'limit': limit,
                        'offset': stickers_offset
                    }
                    
                    stickers_search_response = requests.get(stickers_search_url, params=stickers_search_params, timeout=10)
                    
                    if stickers_search_response.status_code == 200:
                        stickers_data = stickers_search_response.json()
                        stickers_list = stickers_data.get('data', [])
                        stickers_pagination = stickers_data.get('pagination', {})
                        
                        if len(stickers_list) > 0:
                            # Mark stickers as stickers
                            for sticker in stickers_list:
                                sticker['is_sticker'] = True
                            all_search_gifs.extend(stickers_list)
                            stickers_total_count = stickers_pagination.get('total_count', 0)
                            current_stickers_total = len([g for g in all_search_gifs if g.get('is_sticker')])
                            
                            print(f"    Stickers Page {page + 1}: {len(stickers_list)} found (Total stickers: {current_stickers_total}, API total: {stickers_total_count})")
                            
                            if stickers_total_count > 0 and current_stickers_total >= stickers_total_count:
                                break
                            if len(stickers_list) < limit:
                                break
                            
                            stickers_offset += len(stickers_list)
                        else:
                            break
                    else:
                        break
                
                gifs_count = len([g for g in all_search_gifs if not g.get('is_sticker')])
                stickers_count = len([g for g in all_search_gifs if g.get('is_sticker')])
                print(f"  Total uploads found: {len(all_search_gifs)} ({gifs_count} GIFs + {stickers_count} stickers)")
                
                if len(all_search_gifs) > 0:
                    # Extract user info from first GIF (don't break early - we need all GIFs)
                    print(f"  Extracting user info from GIFs...")
                    user_found = False
                    for gif in all_search_gifs:
                        if gif.get('user'):
                            user_from_gif = gif['user']
                            gif_username = user_from_gif.get('username', '').lower()
                            if gif_username == search_lower:
                                if not user_found:
                                    user_data = user_from_gif
                                    print(f"    ✓ FOUND MATCHING USER: {gif_username}")
                                    user_found = True
                                    # Don't break - continue to collect all GIFs
                    
                    # If exact match not found, try first result
                    if not user_data:
                        first_gif = all_search_gifs[0]
                        if first_gif.get('user'):
                            first_user = first_gif['user']
                            first_username = first_user.get('username', '').lower()
                            if search_lower in first_username or first_username in search_lower:
                                user_data = first_user
                                print(f"    ~ Using similar user: {first_username}")
                    
                    # Always store all GIFs found
                    method1_gifs = all_search_gifs.copy()
                    print(f"  Stored {len(method1_gifs)} total uploads for processing")
                
                if not user_data and len(all_search_gifs) > 0:
                    # If still no user_data, use the first GIF's user
                    first_gif = all_search_gifs[0]
                    if first_gif.get('user'):
                        user_data = first_gif['user']
                        method1_gifs = all_search_gifs.copy()
                        print(f"    Using user from first GIF: {user_data.get('username')}")
                else:
                    print(f"  Error response: {gifs_search_response.text[:200]}")
            except Exception as e:
                print(f"Method 1 error: {str(e)}")
                import traceback
                traceback.print_exc()
                pass  # Continue to next method
        
        # Method 2: Try general GIF search with channel name (search GIFs by this username in title/description)
        if not user_data:
            try:
                print(f"\nMethod 2: General GIF search with channel name as query")
                gifs_search_url = f"{GIPHY_API_BASE}/gifs/search"
                gifs_search_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': channel_identifier,  # Search for GIFs with this channel name
                    'limit': 50
                }
                
                print(f"  Query: {channel_identifier}")
                gifs_search_response = requests.get(gifs_search_url, params=gifs_search_params, timeout=10)
                print(f"  Response Status: {gifs_search_response.status_code}")
                
                if gifs_search_response.status_code == 200:
                    gifs_data = gifs_search_response.json()
                    gifs_list = gifs_data.get('data', [])
                    print(f"  Found {len(gifs_list)} GIFs")
                    
                    if len(gifs_list) > 0:
                        # Check if any of these GIFs belong to the user we're looking for
                        print(f"  Checking GIFs for matching user...")
                        for gif in gifs_list:
                            if gif.get('user'):
                                gif_user = gif['user']
                                gif_username = gif_user.get('username', '').lower()
                                print(f"    - GIF from user: {gif_username}")
                                if gif_username == search_lower:
                                    user_data = gif_user
                                    print(f"    ✓ FOUND MATCHING USER: {gif_username}")
                                    break
            except Exception as e:
                print(f"Method 2 error: {str(e)}")
                pass  # Continue to next method
        
        # Method 3: Try direct user lookup by username if available
        # Some channels might be accessible via direct user endpoint
        if not user_data:
            try:
                print(f"\nMethod 3: Direct user lookup by username")
                direct_user_url = f"{GIPHY_API_BASE}/users/{channel_identifier}"
                direct_user_params = {
                    'api_key': GIPHY_API_KEY
                }
                
                direct_user_response = requests.get(direct_user_url, params=direct_user_params, timeout=10)
                print(f"  Response Status: {direct_user_response.status_code}")
                
                if direct_user_response.status_code == 200:
                    direct_user_data = direct_user_response.json()
                    if direct_user_data.get('data'):
                        user_data = direct_user_data['data']
                        print(f"  ✓ Found user via direct lookup: {user_data.get('username')}")
                else:
                    print(f"  Direct lookup failed - endpoint may not exist")
            except Exception as e:
                print(f"Method 3 error: {str(e)}")
                pass  # Continue to next method
        
        print(f"\n{'='*50}")
        if user_data:
            print(f"✓ USER FOUND: {user_data.get('username')}")
        else:
            print(f"✗ User not found via API methods")
        print(f"{'='*50}\n")
        
        # Step 2: If user found via API, fetch all channel data using API
        if user_data:
            results['exists'] = True
            # Store ALL available user data from API - ensure we always have at least channel_id
            results['details'] = {
                'channel_id': channel_identifier,  # Always include channel ID from URL
                'username': user_data.get('username', channel_identifier),
                'display_name': user_data.get('display_name', ''),
                'user_id': user_data.get('id', ''),
                'profile_url': user_data.get('profile_url', f'https://giphy.com/{channel_identifier}'),
                'avatar_url': user_data.get('avatar_url', ''),
                'banner_url': user_data.get('banner_url', ''),
                'description': user_data.get('description', ''),
                'instagram_url': user_data.get('instagram_url', ''),
                'twitter_url': user_data.get('twitter_url', ''),
                'website_url': user_data.get('website_url', ''),
                'is_verified': user_data.get('is_verified', False),
                'is_public': user_data.get('is_public', True),
                'is_authenticated': user_data.get('is_authenticated', False),
                'supply_user_id': user_data.get('supply_user_id', ''),
            }
            
            # Add ALL additional fields from user data (comprehensive)
            for key, value in user_data.items():
                if key not in results['details']:
                    if value is not None and value != '':
                        # Store all non-empty values directly (not with extra_ prefix)
                        results['details'][key] = value
            
            # Step 3: Fetch ALL channel's GIFs using API to get complete analytics
            user_id = user_data.get('id')
            print(f"User ID found: {user_id}")
            
            if user_id:
                # Get user's GIFs using API - fetch with pagination to get ALL data
                gifs_url = f"{GIPHY_API_BASE}/users/{user_id}/gifs"
                gifs_params = {
                    'api_key': GIPHY_API_KEY,
                    'limit': 50,  # Maximum per request
                    'offset': 0
                }
                
                print(f"\nFetching GIFs for user_id: {user_id}")
                print(f"GIFs URL: {gifs_url}")
                gifs_response = requests.get(gifs_url, params=gifs_params, timeout=15)
                print(f"GIFs Response Status: {gifs_response.status_code}")
                
                if gifs_response.status_code == 200:
                    gifs_data = gifs_response.json()
                    gifs_list = gifs_data.get('data', [])
                    pagination = gifs_data.get('pagination', {})
                    
                    # Get total count from pagination
                    total_uploads = pagination.get('total_count', len(gifs_list))
                    gifs_count = len(gifs_list)
                    
                    # Store upload count
                    results['details']['total_uploads'] = total_uploads
                    results['details']['recent_gifs_count'] = gifs_count
                    results['details']['total_gifs_in_api'] = total_uploads
                    
                    # Fetch ALL GIFs with pagination to get complete view count
                    all_gifs = list(gifs_list)  # Start with first batch
                    total_views_all = 0
                    
                    # Calculate views from first batch
                    for gif in gifs_list:
                        views = int(gif.get('views', 0) or 0)
                        total_views_all += views
                    
                    # Fetch remaining GIFs if there are more - fetch ALL GIFs
                    if total_uploads > gifs_count:
                        offset = gifs_count
                        # Increase max requests to fetch ALL GIFs (with reasonable limit to prevent timeout)
                        max_requests = min(50, (total_uploads // 50) + 1)  # Increased limit to fetch more
                        
                        for i in range(max_requests):
                            if offset >= total_uploads:
                                break
                            
                            gifs_params['offset'] = offset
                            more_gifs_response = requests.get(gifs_url, params=gifs_params, timeout=10)
                            
                            if more_gifs_response.status_code == 200:
                                more_gifs_data = more_gifs_response.json()
                                more_gifs_list = more_gifs_data.get('data', [])
                                
                                if not more_gifs_list:
                                    break
                                
                                all_gifs.extend(more_gifs_list)
                                
                                # Calculate views from this batch
                                for gif in more_gifs_list:
                                    views = int(gif.get('views', 0) or 0)
                                    total_views_all += views
                                
                                if len(more_gifs_list) < 50:
                                    break  # Last batch
                                
                                offset += len(more_gifs_list)
                            else:
                                break
                    
                    # Store total views
                    results['details']['total_views'] = total_views_all
                    results['details']['total_views_formatted'] = format_number(total_views_all)
                    
                    # Analyze recent GIFs for detailed analytics (use first batch for detailed analysis)
                    # Use total_views_all for overall stats, analyze recent GIFs for trends
                    accessible_gifs = 0
                    view_counts = []
                    recent_gifs_info = []
                        
                    # Process ALL GIFs from the list to get comprehensive data
                    all_gifs_with_details = []
                    
                    # Process all fetched GIFs (from all_gifs, not just first batch)
                    gifs_to_process = all_gifs if len(all_gifs) > len(gifs_list) else gifs_list
                    
                    for gif in gifs_to_process:  # Process all fetched GIFs
                        gif_id = gif.get('id')
                        gif_views = int(gif.get('views', 0) or 0)
                        gif_url = gif.get('url', f'https://giphy.com/gifs/{gif_id}' if gif_id else '')
                        gif_title = gif.get('title', '')
                        gif_embed_url = gif.get('embed_url', '')
                        gif_import_datetime = gif.get('import_datetime', '')
                        gif_trending_datetime = gif.get('trending_datetime', '')
                        
                        if gif_id:
                            # Get detailed GIF info using API for accurate analytics
                            try:
                                gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
                                gif_detail_params = {
                                    'api_key': GIPHY_API_KEY
                                }
                                gif_detail_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
                                
                                if gif_detail_response.status_code == 200:
                                    accessible_gifs += 1
                                    gif_detail = gif_detail_response.json().get('data', {})
                                    # Get actual view count from detail (more accurate)
                                    actual_views = int(gif_detail.get('views', gif_views) or gif_views)
                                    view_counts.append(actual_views)
                                    
                                    # Get image URLs for display
                                    images = gif_detail.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    original = images.get('original', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif_detail.get('title', gif_title),
                                        'views': actual_views,
                                        'url': gif_detail.get('url', gif_url),
                                        'embed_url': gif_detail.get('embed_url', gif_embed_url),
                                        'import_datetime': gif_detail.get('import_datetime', gif_import_datetime),
                                        'trending_datetime': gif_detail.get('trending_datetime', gif_trending_datetime),
                                        'rating': gif_detail.get('rating', ''),
                                        'accessible': True,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', ''),
                                        'original_url': original.get('url', '')
                                    })
                                else:
                                    # Can't get detail, but GIF is in the list so it's accessible
                                    accessible_gifs += 1
                                    view_counts.append(gif_views)
                                    # Get image URLs from gif object if available
                                    images = gif.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif_title,
                                        'views': gif_views,
                                        'url': gif_url,
                                        'embed_url': gif_embed_url,
                                        'import_datetime': gif_import_datetime,
                                        'trending_datetime': gif_trending_datetime,
                                        'accessible': True,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', '')
                                    })
                            except Exception as e:
                                # If detail fetch fails, GIF is still accessible (it's in the list)
                                accessible_gifs += 1
                                view_counts.append(gif_views)
                                # Get image URLs from gif object if available
                                images = gif.get('images', {})
                                fixed_height = images.get('fixed_height', {})
                                fixed_height_small = images.get('fixed_height_small', {})
                                
                                all_gifs_with_details.append({
                                    'id': gif_id,
                                    'title': gif_title,
                                    'views': gif_views,
                                    'url': gif_url,
                                    'embed_url': gif_embed_url,
                                    'import_datetime': gif_import_datetime,
                                    'trending_datetime': gif_trending_datetime,
                                    'accessible': True,
                                    'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                    'preview_url': fixed_height.get('url', '')
                                })
                        else:
                            # No GIF ID but we have the GIF object
                            if gif_views > 0:
                                accessible_gifs += 1
                                view_counts.append(gif_views)
                                # Get image URLs from gif object if available
                                images = gif.get('images', {})
                                fixed_height = images.get('fixed_height', {})
                                fixed_height_small = images.get('fixed_height_small', {})
                                
                                all_gifs_with_details.append({
                                    'id': '',
                                    'title': gif_title,
                                    'views': gif_views,
                                    'url': gif_url,
                                    'accessible': True,
                                    'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                    'preview_url': fixed_height.get('url', '')
                                })
                    
                    # Store ALL GIFs info (not limited)
                    recent_gifs_info = all_gifs_with_details  # Store all GIFs for display
                    
                    # If we have GIFs in the list, count them all as accessible
                    if gifs_count > 0 and accessible_gifs == 0:
                        # Fallback: if detail checks all failed, still count GIFs as accessible
                        accessible_gifs = min(gifs_count, 10)  # At least the ones we checked
                        for gif in gifs_list[:10]:
                            gif_views = int(gif.get('views', 0) or 0)
                            if gif_views > 0:
                                view_counts.append(gif_views)
                                total_views += gif_views
                    
                    # Calculate view trends and daily growth analysis
                    views_increasing = False
                    daily_growth_rate = 0
                    shadow_ban_indicator = False
                    
                    if len(view_counts) >= 2:
                        # Compare first half vs second half of recent GIFs
                        mid_point = len(view_counts) // 2
                        older_avg = sum(view_counts[:mid_point]) / mid_point if mid_point > 0 else 0
                        newer_avg = sum(view_counts[mid_point:]) / (len(view_counts) - mid_point) if len(view_counts) > mid_point else 0
                        
                        if older_avg > 0:
                            # Calculate percentage increase
                            percent_increase = ((newer_avg - older_avg) / older_avg) * 100
                            views_increasing = newer_avg > older_avg * 1.1  # 10% increase threshold
                            
                            # Estimate daily growth rate (assuming recent GIFs are newer)
                            # If we have import dates, use them; otherwise estimate based on position
                            if len(all_gifs_with_details) >= 2:
                                # Calculate average views per day for recent GIFs
                                # Estimate: newer GIFs should have lower views if growing normally
                                # If views are very similar and low, might indicate shadow ban
                                view_differences = []
                                for i in range(1, min(10, len(view_counts))):
                                    if view_counts[i-1] > 0:
                                        diff = view_counts[i] - view_counts[i-1]
                                        view_differences.append(diff)
                                
                                if view_differences:
                                    avg_daily_growth = sum(view_differences) / len(view_differences)
                                    daily_growth_rate = avg_daily_growth
                                    
                                    # Shadow ban detection: if daily growth is only 15-20 views
                                    if 15 <= avg_daily_growth <= 25 and newer_avg < 1000:
                                        shadow_ban_indicator = True
                                    # Also check if views are very low and stagnant
                                    elif newer_avg < 100 and abs(percent_increase) < 5:
                                        shadow_ban_indicator = True
                    
                    # Add comprehensive analytics to results
                    # Use total_views_all (calculated from all GIFs)
                    final_total_views = total_views_all
                    results['details']['total_views'] = final_total_views
                    results['details']['total_views_formatted'] = format_number(final_total_views)
                    results['details']['average_views_per_gif'] = final_total_views / total_uploads if total_uploads > 0 else 0
                    results['details']['accessible_gifs_count'] = accessible_gifs if accessible_gifs > 0 else min(gifs_count, 10)
                    results['details']['total_gifs_checked'] = len(recent_gifs_info) if recent_gifs_info else min(gifs_count, 10)
                    results['details']['views_increasing'] = views_increasing
                    results['details']['daily_growth_rate'] = daily_growth_rate
                    results['details']['shadow_ban_indicator'] = shadow_ban_indicator
                    results['details']['all_gifs'] = all_gifs_with_details  # Store ALL GIFs with details
                    results['details']['recent_gifs'] = recent_gifs_info if recent_gifs_info else []  # All GIFs for display
                    
                    # Add summary statistics
                    results['details']['total_gifs_analyzed'] = len(all_gifs) if 'all_gifs' in locals() else gifs_count
                    results['details']['gifs_fetched'] = len(all_gifs) if 'all_gifs' in locals() else gifs_count
                    
                    # Store channel and GIF data in database
                    store_channel_data(channel_identifier, user_data.get('username'), user_data.get('id'), 
                                     user_data.get('display_name'), user_data.get('profile_url'))
                    for gif in all_gifs_with_details:
                        if gif.get('id'):
                            store_gif_data(gif.get('id'), channel_identifier, gif.get('title'), gif.get('url'))
                    
                    # Apply analysis logic for channels with working /users/{user_id}/gifs endpoint
                    # auto_check_views=True to automatically scrape views if not in database
                    # Pass accessible_gifs count (all GIFs from working endpoint are accessible)
                    analysis_result = analyze_channel_status(user_data, all_gifs_with_details, user_id, False, channel_identifier, auto_check_views=True, gifs_accessible_via_detail=accessible_gifs)
                    results.update(analysis_result)
                    
                    # Store analysis reasons in details for frontend display
                    if analysis_result.get('analysis_reasons'):
                        results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                elif gifs_response.status_code == 403:
                    results['banned'] = True
                    results['status'] = 'banned'
                    results['working'] = False
                elif gifs_response.status_code == 404:
                    # User exists but GIFs endpoint returns 404 - use GIFs from Method 1 search instead
                    print(f"GIFs endpoint returned 404. Using GIFs found in Method 1 search...")
                    if 'method1_gifs' in locals() and len(method1_gifs) > 0:
                        print(f"Processing {len(method1_gifs)} GIFs from Method 1...")
                        
                        # Process GIFs and check accessibility via detail endpoint
                        all_gifs_with_details = []
                        accessible_gifs_via_detail = 0  # Track how many GIFs are accessible
                        
                        # Check first 10 GIFs for accessibility (sample)
                        sample_size = min(10, len(method1_gifs))
                        print(f"  Checking accessibility of {sample_size} GIFs via detail endpoint...")
                        time.sleep(0.2)  # Small delay before starting checks
                        
                        total_views_all = 0
                        for idx, gif in enumerate(method1_gifs):
                            gif_id = gif.get('id')
                            gif_views = 0
                            if gif_id:
                                # Check if GIF is accessible via detail endpoint and fetch views
                                is_accessible = False
                                try:
                                    gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
                                    gif_detail_response = requests.get(gif_detail_url, params={'api_key': GIPHY_API_KEY}, timeout=5)
                                    if gif_detail_response.status_code == 200:
                                        is_accessible = True
                                        if idx < sample_size:
                                            accessible_gifs_via_detail += 1
                                            print(f"    ✓ GIF {gif_id[:12]}... is accessible via detail endpoint")
                                        
                                        # Get views from detail endpoint
                                        gif_detail = gif_detail_response.json().get('data', {})
                                        gif_views = int(gif_detail.get('views', gif.get('views', 0)) or 0)
                                        total_views_all += gif_views
                                        
                                        # Use images from detail if available
                                        images = gif_detail.get('images', gif.get('images', {}))
                                        fixed_height = images.get('fixed_height', {})
                                        fixed_height_small = images.get('fixed_height_small', {})
                                        original = images.get('original', {})
                                        
                                        all_gifs_with_details.append({
                                            'id': gif_id,
                                            'title': gif_detail.get('title', gif.get('title', '')),
                                            'views': gif_views,
                                            'url': gif_detail.get('url', gif.get('url', f'https://giphy.com/gifs/{gif_id}')),
                                            'embed_url': gif_detail.get('embed_url', gif.get('embed_url', '')),
                                            'accessible': is_accessible,
                                            'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                            'preview_url': fixed_height.get('url', ''),
                                            'original_url': original.get('url', ''),
                                            'rating': gif_detail.get('rating', gif.get('rating', '')),
                                            'is_sticker': gif.get('is_sticker', False),
                                            'type': 'sticker' if gif.get('is_sticker') else 'gif'
                                        })
                                    else:
                                        if idx < sample_size:
                                            print(f"    ✗ GIF {gif_id[:12]}... returned {gif_detail_response.status_code}")
                                        # Use basic info if detail fetch fails
                                        gif_views = int(gif.get('views', 0) or 0)
                                        total_views_all += gif_views
                                        images = gif.get('images', {})
                                        fixed_height = images.get('fixed_height', {})
                                        fixed_height_small = images.get('fixed_height_small', {})
                                        original = images.get('original', {})
                                        
                                        all_gifs_with_details.append({
                                            'id': gif_id,
                                            'title': gif.get('title', ''),
                                            'views': gif_views,
                                            'url': gif.get('url', f'https://giphy.com/gifs/{gif_id}'),
                                            'embed_url': gif.get('embed_url', ''),
                                            'accessible': False,
                                            'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                            'preview_url': fixed_height.get('url', ''),
                                            'original_url': original.get('url', ''),
                                            'rating': gif.get('rating', ''),
                                            'is_sticker': gif.get('is_sticker', False),
                                            'type': 'sticker' if gif.get('is_sticker') else 'gif'
                                        })
                                except Exception as e:
                                    if idx < sample_size:
                                        print(f"    ✗ GIF {gif_id[:12]}... error: {str(e)[:30]}")
                                    # Use basic info if detail fetch fails
                                    gif_views = int(gif.get('views', 0) or 0)
                                    total_views_all += gif_views
                                    images = gif.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    original = images.get('original', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif.get('title', ''),
                                        'views': gif_views,
                                        'url': gif.get('url', f'https://giphy.com/gifs/{gif_id}'),
                                        'embed_url': gif.get('embed_url', ''),
                                        'accessible': False,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', ''),
                                        'original_url': original.get('url', ''),
                                        'rating': gif.get('rating', ''),
                                        'is_sticker': gif.get('is_sticker', False),
                                        'type': 'sticker' if gif.get('is_sticker') else 'gif'
                                    })
                                
                                # Small delay to avoid rate limiting
                                if idx > 0 and idx % 10 == 0:
                                    time.sleep(0.1)
                            
                            if (idx + 1) % 20 == 0:
                                print(f"  Processed {idx + 1}/{len(method1_gifs)} uploads... (Total views so far: {total_views_all:,})")
                        
                        print(f"  ✓ Processed all GIFs")
                        print(f"  Accessibility check completed: {accessible_gifs_via_detail}/{sample_size} GIFs accessible in checked sample")
                            
                        # Store the processed GIFs
                        results['details']['total_uploads'] = len(all_gifs_with_details)
                        results['details']['recent_gifs_count'] = len(all_gifs_with_details)
                        results['details']['total_gifs_in_api'] = len(all_gifs_with_details)
                        results['details']['total_views'] = total_views_all
                        results['details']['total_views_formatted'] = format_number(total_views_all)
                        results['details']['average_views_per_gif'] = total_views_all / len(all_gifs_with_details) if len(all_gifs_with_details) > 0 else 0
                        results['details']['all_gifs'] = all_gifs_with_details
                        results['details']['recent_gifs'] = all_gifs_with_details
                        
                        # Store channel and GIF data in database
                        if user_data:
                            store_channel_data(channel_identifier, user_data.get('username'), user_data.get('id'), 
                                             user_data.get('display_name'), user_data.get('profile_url'))
                        for gif in all_gifs_with_details:
                            if gif.get('id'):
                                store_gif_data(gif.get('id'), channel_identifier, gif.get('title'), gif.get('url'))
                        
                        # Apply analysis logic to determine channel status
                        # auto_check_views=True to automatically scrape views if not in database
                        # Pass accessible_gifs_via_detail to help differentiate shadow banned vs working
                        # Calculate estimated accessible count
                        if accessible_gifs_via_detail > 0 and sample_size > 0:
                            # Extrapolate: if X out of sample_size are accessible, estimate for all
                            accessible_ratio = accessible_gifs_via_detail / sample_size
                            accessible_count = int(accessible_ratio * len(method1_gifs))
                            print(f"  Accessibility summary: {accessible_gifs_via_detail}/{sample_size} checked accessible, estimated {accessible_count}/{len(method1_gifs)} total ({accessible_ratio*100:.1f}%)")
                        elif sample_size == len(method1_gifs):
                            # Checked all GIFs
                            accessible_count = accessible_gifs_via_detail
                            print(f"  Accessibility summary: {accessible_gifs_via_detail}/{len(method1_gifs)} GIFs accessible ({accessible_count/len(method1_gifs)*100:.1f}%)")
                        else:
                            # No accessibility data - use sample size as estimate
                            accessible_count = 0
                            print(f"  Accessibility summary: No GIFs accessible in checked sample")
                        
                        analysis_result = analyze_channel_status(user_data, all_gifs_with_details, user_id, True, channel_identifier, auto_check_views=True, gifs_accessible_via_detail=accessible_count)
                        results.update(analysis_result)
                        
                        # Store analysis reasons in details for frontend display
                        if analysis_result.get('analysis_reasons'):
                            results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                        
                        print(f"✓ Processed {len(all_gifs_with_details)} uploads")
                        print(f"✓ Analysis: Status={results.get('status')}, Shadow Banned={results.get('shadow_banned')}, Working={results.get('working')}")
                    else:
                        # No GIFs from Method 1 - analyze status
                        if user_data:
                            store_channel_data(channel_identifier, user_data.get('username'), user_data.get('id'), 
                                             user_data.get('display_name'), user_data.get('profile_url'))
                        analysis_result = analyze_channel_status(user_data, [], user_id, True, channel_identifier, auto_check_views=False)
                        results.update(analysis_result)
                        # Store analysis reasons in details for frontend display
                        if analysis_result.get('analysis_reasons'):
                            results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                        # Store analysis reasons in details for frontend display
                        if analysis_result.get('analysis_reasons'):
                            results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                else:
                        # Other error - try to get info from user data alone
                        # If user exists and has profile, assume working but with limited access
                        if results['exists'] and results['details'].get('username'):
                            results['working'] = True
                            results['status'] = 'working'
                            results['shadow_banned'] = False
                            results['error'] = f"Could not fetch GIFs list (status {gifs_response.status_code}), but user exists"
                        else:
                            results['shadow_banned'] = True
                            results['status'] = 'shadow_banned'
                            results['working'] = False
            else:
                # User found but no user_id - use the GIFs we found in Method 1
                if 'method1_gifs' in locals() and len(method1_gifs) > 0:
                    print(f"User found but no user_id. Processing {len(method1_gifs)} GIFs from Method 1 search with detailed views...")
                    
                    # Process each GIF individually to get accurate view counts
                    all_gifs_with_details = []
                    total_views_all = 0
                    
                    for gif in method1_gifs:
                        gif_id = gif.get('id')
                        if gif_id:
                            # Fetch detailed GIF info to get accurate views
                            try:
                                gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
                                gif_detail_params = {'api_key': GIPHY_API_KEY}
                                gif_detail_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
                                
                                if gif_detail_response.status_code == 200:
                                    gif_detail = gif_detail_response.json().get('data', {})
                                    actual_views = int(gif_detail.get('views', gif.get('views', 0)) or 0)
                                    
                                    images = gif_detail.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    original = images.get('original', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif_detail.get('title', gif.get('title', '')),
                                        'views': actual_views,
                                        'url': gif_detail.get('url', gif.get('url', '')),
                                        'embed_url': gif_detail.get('embed_url', gif.get('embed_url', '')),
                                        'accessible': True,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', ''),
                                        'original_url': original.get('url', '')
                                    })
                                    total_views_all += actual_views
                                else:
                                    # Use basic info if detail fetch fails
                                    gif_views = int(gif.get('views', 0) or 0)
                                    images = gif.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif.get('title', ''),
                                        'views': gif_views,
                                        'url': gif.get('url', ''),
                                        'accessible': True,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', '')
                                    })
                                    total_views_all += gif_views
                            except Exception as e:
                                # Still add the GIF with basic info
                                gif_views = int(gif.get('views', 0) or 0)
                                images = gif.get('images', {})
                                fixed_height = images.get('fixed_height', {})
                                fixed_height_small = images.get('fixed_height_small', {})
                                
                                all_gifs_with_details.append({
                                    'id': gif_id,
                                    'title': gif.get('title', ''),
                                    'views': gif_views,
                                    'url': gif.get('url', ''),
                                    'accessible': True,
                                    'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                    'preview_url': fixed_height.get('url', '')
                                })
                                total_views_all += gif_views
                    
                    results['details']['total_uploads'] = len(all_gifs_with_details)
                    results['details']['recent_gifs_count'] = len(all_gifs_with_details)
                    results['details']['total_views'] = total_views_all
                    results['details']['total_views_formatted'] = format_number(total_views_all)
                    results['details']['average_views_per_gif'] = total_views_all / len(all_gifs_with_details) if len(all_gifs_with_details) > 0 else 0
                    results['details']['all_gifs'] = all_gifs_with_details
                    results['details']['recent_gifs'] = all_gifs_with_details
                    
                    # Store channel and GIF data in database
                    if user_data:
                        store_channel_data(channel_identifier, user_data.get('username'), user_data.get('id'), 
                                         user_data.get('display_name'), user_data.get('profile_url'))
                    for gif in all_gifs_with_details:
                        if gif.get('id'):
                            store_gif_data(gif.get('id'), channel_identifier, gif.get('title'), gif.get('url'))
                    
                    # Apply analysis logic
                    # auto_check_views=True to automatically scrape views if not in database
                    # No accessibility data for this path (username-only search)
                    analysis_result = analyze_channel_status(user_data, all_gifs_with_details, None, False, channel_identifier, auto_check_views=True, gifs_accessible_via_detail=None)
                    results.update(analysis_result)
                    
                    # Store analysis reasons in details for frontend display
                    if analysis_result.get('analysis_reasons'):
                        results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                    
                    print(f"✓ Processed {len(all_gifs_with_details)} GIFs with {total_views_all:,} total views")
                    print(f"✓ Analysis: Status={results.get('status')}, Shadow Banned={results.get('shadow_banned')}, Working={results.get('working')}")
                else:
                    results['status'] = 'unknown'
                    results['error'] = 'User found but no user_id and no GIFs available'
        
        # Check if we successfully found user and processed their data
        if user_data and results.get('exists'):
            print(f"\n✓ Final Results:")
            print(f"  Exists: {results.get('exists')}")
            print(f"  Status: {results.get('status')}")
            print(f"  GIFs: {len(results.get('details', {}).get('all_gifs', []))}")
            print(f"  Total Views: {results.get('details', {}).get('total_views', 0)}")
        
        if not user_data:
            # User not found in API search - try alternative methods to get channel info
            # Method 1: Search GIFs by username to extract user info
            # Method 2: Try web scraping
            # Method 3: Search GIFs with channel name
            
            found_via_gifs = False
            
            # Method 1: Try searching GIFs by username parameter (most reliable)
            try:
                gifs_by_user_url = f"{GIPHY_API_BASE}/gifs/search"
                gifs_by_user_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': '',  # Empty query
                    'username': channel_identifier,  # Search by username
                    'limit': 10
                }
                
                gifs_by_user_response = requests.get(gifs_by_user_url, params=gifs_by_user_params, timeout=10)
                
                if gifs_by_user_response.status_code == 200:
                    gifs_data = gifs_by_user_response.json()
                    gifs_list = gifs_data.get('data', [])
                    print(f"Found {len(gifs_list)} GIFs in fallback search")
                    
                    if len(gifs_list) > 0:
                        # Extract user info from GIFs
                        for gif in gifs_list:
                            if gif.get('user'):
                                gif_user = gif['user']
                                gif_username = gif_user.get('username', '').lower()
                                print(f"  Checking GIF from user: {gif_username}")
                                if gif_username == search_lower:
                                    user_data = gif_user
                                    found_via_gifs = True
                                    print(f"  ✓ Found matching user: {gif_username}")
                                    break
                        
                        if found_via_gifs and user_data:
                            # Found user via GIFs - now fetch full details
                            results['exists'] = True
                            results['details'] = {
                                'username': user_data.get('username', channel_identifier),
                                'display_name': user_data.get('display_name', ''),
                                'user_id': user_data.get('id', ''),
                                'profile_url': user_data.get('profile_url', ''),
                                'avatar_url': user_data.get('avatar_url', ''),
                                'description': user_data.get('description', ''),
                                'instagram_url': user_data.get('instagram_url', ''),
                                'twitter_url': user_data.get('twitter_url', ''),
                                'website_url': user_data.get('website_url', ''),
                                'note': 'Found via GIF search'
                            }
                            
                            # Now get GIFs count and analytics
                            user_id = user_data.get('id')
                            if user_id:
                                gifs_url = f"{GIPHY_API_BASE}/users/{user_id}/gifs"
                                gifs_params = {
                                    'api_key': GIPHY_API_KEY,
                                    'limit': 25,
                                    'offset': 0
                                }
                                
                                gifs_response = requests.get(gifs_url, params=gifs_params, timeout=10)
                                if gifs_response.status_code == 200:
                                    gifs_list_data = gifs_response.json()
                                    gifs_list = gifs_list_data.get('data', [])
                                    results['details']['recent_gifs_count'] = len(gifs_list)
                                    
                                    # Calculate views
                                    total_views = 0
                                    for gif in gifs_list[:10]:
                                        views = int(gif.get('views', 0) or 0)
                                        total_views += views
                                    
                                    results['details']['total_views'] = total_views
                                    results['details']['average_views_per_gif'] = total_views / len(gifs_list) if len(gifs_list) > 0 else 0
                                    
                                    if len(gifs_list) > 0:
                                        results['working'] = True
                                        results['status'] = 'working'
                                        results['shadow_banned'] = False
                                    else:
                                        results['working'] = False
                                        results['status'] = 'working'  # User exists
                                        results['shadow_banned'] = False
                                
                                return results
            except Exception as e:
                pass
            
            # Method 2: Try web scraping if GIF search didn't work
            if not found_via_gifs and original_url:
                web_result = check_channel_via_web_scraping(channel_identifier, original_url)
                if web_result.get('exists') or web_result.get('working'):
                    return web_result
            
            # Method 3: Try general GIF search with channel name (search for GIFs by this username)
            if not found_via_gifs:
                try:
                    gifs_search_url = f"{GIPHY_API_BASE}/gifs/search"
                    gifs_search_params = {
                        'api_key': GIPHY_API_KEY,
                        'q': channel_identifier,
                        'limit': 25  # Get more GIFs
                    }
                    gifs_search_response = requests.get(gifs_search_url, params=gifs_search_params, timeout=10)
                    
                    if gifs_search_response.status_code == 200:
                        gifs_data = gifs_search_response.json()
                        gifs_list = gifs_data.get('data', [])
                        
                        if len(gifs_list) > 0:
                            # Check if any of these GIFs belong to the user we're looking for
                            user_from_gifs = None
                            matching_gifs = []
                            
                            for gif in gifs_list:
                                gif_user = gif.get('user')
                                if gif_user and gif_user.get('username', '').lower() == search_lower:
                                    if not user_from_gifs:
                                        user_from_gifs = gif_user
                                    matching_gifs.append(gif)
                            
                            # If we found matching GIFs, extract user info and fetch all their GIFs
                            if user_from_gifs and len(matching_gifs) > 0:
                                user_data = user_from_gifs
                                # Set found_via_gifs to trigger proper processing below
                                found_via_gifs = True
                                
                                # Now process as if we found the user
                                results['exists'] = True
                                results['details'] = {
                                    'channel_id': channel_identifier,
                                    'username': user_from_gifs.get('username', channel_identifier),
                                    'display_name': user_from_gifs.get('display_name', ''),
                                    'user_id': user_from_gifs.get('id', ''),
                                    'profile_url': user_from_gifs.get('profile_url', f'https://giphy.com/{channel_identifier}'),
                                    'avatar_url': user_from_gifs.get('avatar_url', ''),
                                    'description': user_from_gifs.get('description', ''),
                                    'note': 'Found via GIF search by channel name'
                                }
                                
                                # Get user ID and fetch all their GIFs
                                user_id = user_from_gifs.get('id')
                                if user_id:
                                    # Fetch all GIFs for this user
                                    gifs_url = f"{GIPHY_API_BASE}/users/{user_id}/gifs"
                                    gifs_params = {
                                        'api_key': GIPHY_API_KEY,
                                        'limit': 50,
                                        'offset': 0
                                    }
                                    
                                    gifs_response = requests.get(gifs_url, params=gifs_params, timeout=15)
                                    if gifs_response.status_code == 200:
                                        user_gifs_data = gifs_response.json()
                                        user_gifs_list = user_gifs_data.get('data', [])
                                        pagination = user_gifs_data.get('pagination', {})
                                        total_uploads = pagination.get('total_count', len(user_gifs_list))
                                        
                                        results['details']['total_uploads'] = total_uploads
                                        results['details']['recent_gifs_count'] = len(user_gifs_list)
                                        
                                        # Process GIFs and calculate views (similar to main processing)
                                        total_views_all = 0
                                        all_gifs_with_details = []
                                        
                                        for gif in user_gifs_list:
                                            gif_id = gif.get('id')
                                            gif_views = int(gif.get('views', 0) or 0)
                                            total_views_all += gif_views
                                            
                                            images = gif.get('images', {})
                                            fixed_height = images.get('fixed_height', {})
                                            fixed_height_small = images.get('fixed_height_small', {})
                                            
                                            all_gifs_with_details.append({
                                                'id': gif_id,
                                                'title': gif.get('title', ''),
                                                'views': gif_views,
                                                'url': gif.get('url', f'https://giphy.com/gifs/{gif_id}' if gif_id else ''),
                                                'accessible': True,
                                                'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                                'preview_url': fixed_height.get('url', '')
                                            })
                                        
                                        results['details']['total_views'] = total_views_all
                                        results['details']['total_views_formatted'] = format_number(total_views_all)
                                        results['details']['all_gifs'] = all_gifs_with_details
                                        results['details']['recent_gifs'] = all_gifs_with_details
                                        
                                        if len(user_gifs_list) > 0:
                                            results['working'] = True
                                            results['status'] = 'working'
                                            results['shadow_banned'] = False
                                        
                                        return results
                            
                            # If we found GIFs but couldn't match user, still show the GIFs
                            elif len(gifs_list) > 0:
                                results['exists'] = True
                                results['status'] = 'working'
                                results['working'] = True
                                results['shadow_banned'] = False
                                results['details'] = {
                                    'channel_id': channel_identifier,
                                    'username': channel_identifier,
                                    'recent_gifs_count': len(gifs_list),
                                    'total_uploads': len(gifs_list),
                                    'profile_url': f'https://giphy.com/{channel_identifier}',
                                    'note': 'Found GIFs but user profile not fully accessible via API'
                                }
                                
                                # Fetch individual GIF details to get actual view counts for analysis
                                total_views = 0
                                all_gifs_with_details = []
                                
                                for gif in gifs_list:
                                    gif_id = gif.get('id', '')
                                    views = 0
                                    
                                    if gif_id:
                                        # Fetch detailed GIF info to get actual view counts
                                        try:
                                            gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
                                            gif_detail_params = {'api_key': GIPHY_API_KEY}
                                            gif_detail_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
                                            
                                            if gif_detail_response.status_code == 200:
                                                gif_detail = gif_detail_response.json().get('data', {})
                                                views = int(gif_detail.get('views', gif.get('views', 0)) or 0)
                                        except:
                                            views = int(gif.get('views', 0) or 0)
                                    else:
                                        views = int(gif.get('views', 0) or 0)
                                    
                                    total_views += views
                                    
                                    images = gif.get('images', {})
                                    fixed_height = images.get('fixed_height', {})
                                    fixed_height_small = images.get('fixed_height_small', {})
                                    
                                    all_gifs_with_details.append({
                                        'id': gif_id,
                                        'title': gif.get('title', ''),
                                        'views': views,
                                        'url': gif.get('url', ''),
                                        'accessible': True,
                                        'thumbnail_url': fixed_height_small.get('url', fixed_height.get('url', '')),
                                        'preview_url': fixed_height.get('url', '')
                                    })
                                
                                results['details']['total_views'] = total_views
                                results['details']['total_views_formatted'] = format_number(total_views)
                                results['details']['all_gifs'] = all_gifs_with_details
                                results['details']['recent_gifs'] = all_gifs_with_details
                                
                                # Store channel and GIF data in database
                                store_channel_data(channel_identifier, channel_identifier, None, None, f'https://giphy.com/{channel_identifier}')
                                for gif in all_gifs_with_details:
                                    if gif.get('id'):
                                        store_gif_data(gif.get('id'), channel_identifier, gif.get('title'), gif.get('url'))
                                
                                # Apply analysis logic
                                analysis_result = analyze_channel_status(user_data if 'user_data' in locals() else None, all_gifs_with_details, None, False, channel_identifier, auto_check_views=True)
                                results.update(analysis_result)
                                
                                # Store analysis reasons in details for frontend display
                                if analysis_result.get('analysis_reasons'):
                                    results['details']['analysis_reasons'] = analysis_result['analysis_reasons']
                                
                                return results
                except Exception as e:
                    results['error'] = f'Error during GIF search: {str(e)}'
                    pass
            
            # Final fallback - Try web scraping one more time if we haven't already
            if original_url:
                try:
                    web_result = check_channel_via_web_scraping(channel_identifier, original_url)
                    if web_result.get('exists') or web_result.get('working') or web_result.get('status') != 'not_found':
                        return web_result
                except Exception as e:
                    pass
            
            # Final fallback - check search visibility before marking as banned/not_found
            # Search for channel name in Giphy - if no GIFs found, it's BANNED
            print(f"\n{'='*50}")
            print(f"Final check: Searching for channel '{channel_identifier}' in Giphy search results")
            print(f"{'='*50}")
            try:
                search_visibility = check_channel_in_search_results(
                    channel_identifier,
                    sample_gif_ids=None,
                    all_gifs_list=None
                )
                if search_visibility and not search_visibility.get('error'):
                    visible_in_search = search_visibility.get('visible_in_search', False)
                    matching_count = search_visibility.get('matching_gifs_count', 0)
                    queries_tested = search_visibility.get('search_queries_tested', [])
                    
                    if not visible_in_search:
                        # Channel name not found in search results = BANNED
                        print(f"  🚫 Channel '{channel_identifier}' not found in search results (no GIFs/views)")
                        print(f"     Tested queries: {', '.join(queries_tested[:5])}")
                        results['exists'] = True  # Channel exists (we searched for it), just banned
                        results['status'] = 'banned'
                        results['banned'] = True
                        results['shadow_banned'] = False
                        results['working'] = False
                        results['details'] = {
                            'username': channel_identifier,
                            'search_visibility': search_visibility,
                            'note': f'Channel "{channel_identifier}" not found in search results - no GIFs/views found = BANNED'
                        }
                        results['error'] = f'Channel "{channel_identifier}" not found in Giphy search results. Channel is banned.'
                    else:
                        # Channel found in search but API failed - unusual case
                        print(f"  ⚠️  Channel '{channel_identifier}' found in search ({matching_count} GIFs) but API failed")
                        results['exists'] = True
                        results['status'] = 'unknown'
                        results['error'] = f'Channel found in search but API lookup failed'
                else:
                    # Search check failed - mark as not_found
                    print(f"  ⚠️  Search check failed - marking as not_found")
                    results['exists'] = False
                    results['status'] = 'not_found'
            except Exception as e:
                print(f"  ⚠️  Search check error: {str(e)} - marking as not_found")
                results['exists'] = False
                results['status'] = 'not_found'
        
        if response_status == 403:
            results['banned'] = True
            results['status'] = 'banned'
            results['working'] = False
        elif response_status == 429:
            # Rate limit exceeded - fallback to web scraping
            results['error'] = 'API rate limit exceeded. Trying web scraping...'
            if original_url:
                return check_channel_via_web_scraping(channel_identifier, original_url)
            results['status'] = 'error'
        else:
            results['error'] = f"API returned status code: {response.status_code}"
            # Fallback to web scraping on API errors
            if original_url and response.status_code != 403:
                return check_channel_via_web_scraping(channel_identifier, original_url)
            results['status'] = 'error'
            
    except requests.exceptions.RequestException as e:
        # API failed - try web scraping as fallback ONLY if we didn't find the user
        if not user_data and original_url:
            return check_channel_via_web_scraping(channel_identifier, original_url)
        if not results.get('exists'):
            results['error'] = str(e)
            results['status'] = 'error'
    except Exception as e:
        # Try web scraping as fallback ONLY if we didn't find the user
        if not user_data and original_url:
            return check_channel_via_web_scraping(channel_identifier, original_url)
        if not results.get('exists'):
            results['error'] = str(e)
            results['status'] = 'error'
    
    # Don't overwrite results if we successfully found the user
    if results.get('exists') and results.get('details', {}).get('all_gifs'):
        print(f"\n✓ Final check: Successfully returning results with {len(results['details']['all_gifs'])} GIFs")
    
    return results

@app.route('/api/update-views', methods=['POST'])
def update_views():
    """
    API endpoint to manually trigger view count updates for a channel.
    Useful for daily updates or manual refresh.
    """
    data = request.json
    channel_id = data.get('channel_id', '').strip()
    
    if not channel_id:
        return jsonify({'error': 'channel_id is required'}), 400
    
    try:
        # Get all GIFs for this channel
        gifs = get_channel_gifs(channel_id)
        
        if not gifs:
            return jsonify({'error': f'No GIFs found for channel: {channel_id}'}), 404
        
        gif_ids = [gif['gif_id'] for gif in gifs]
        
        # Update views
        results = update_gif_views_batch(gif_ids, max_workers=5)
        
        successful = sum(1 for r in results if r.get('success'))
        failed = len(results) - successful
        
        return jsonify({
            'success': True,
            'channel_id': channel_id,
            'total_gifs': len(gif_ids),
            'successful_updates': successful,
            'failed_updates': failed,
            'results': results
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-view-history', methods=['GET'])
def get_view_history():
    """
    API endpoint to get view history for a GIF or channel.
    """
    gif_id = request.args.get('gif_id')
    channel_id = request.args.get('channel_id')
    days = int(request.args.get('days', 7))
    
    try:
        if gif_id:
            # Get history for single GIF
            history = get_gif_view_history(gif_id, days)
            return jsonify({
                'success': True,
                'gif_id': gif_id,
                'history': history
            })
        elif channel_id:
            # Get history for all GIFs in channel
            gifs = get_channel_gifs(channel_id)
            history_data = {}
            for gif in gifs:
                history = get_gif_view_history(gif['gif_id'], days)
                history_data[gif['gif_id']] = history
            return jsonify({
                'success': True,
                'channel_id': channel_id,
                'history': history_data
            })
        else:
            return jsonify({'error': 'gif_id or channel_id is required'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-channel-views-graph', methods=['GET'])
def get_channel_views_graph():
    """
    API endpoint to get channel view history formatted for graphing.
    Returns cumulative total views per date (similar to Giphy dashboard graph).
    
    Query parameters:
        channel_id: Required - Channel ID to get graph data for
        days: Optional - Number of days to look back (default 30)
    
    Returns:
        {
            "success": true,
            "channel_id": "username",
            "dates": ["2024-11-30", "2024-12-07", ...],
            "total_views": [0, 1000000, 2000000, ...],
            "data_points": [
                {"date": "2024-11-30", "views": 0},
                {"date": "2024-12-07", "views": 1000000},
                ...
            ],
            "total_data_points": 10,
            "note": "Giphy API doesn't provide historical data. This uses our stored database history."
        }
    """
    channel_id = request.args.get('channel_id')
    days = int(request.args.get('days', 30))
    
    if not channel_id:
        return jsonify({'error': 'channel_id is required'}), 400
    
    try:
        graph_data = get_channel_views_history_graph(channel_id, days)
        graph_data['success'] = True
        graph_data['note'] = "Giphy API doesn't provide historical data. This uses our stored database history from daily view updates."
        return jsonify(graph_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-realtime-views', methods=['GET'])
def get_realtime_views():
    """
    REAL-TIME API endpoint to get current channel views from Giphy API and compare with last fetch.
    No database storage - uses lightweight JSON cache file.
    
    Query parameters:
        channel_id: Required - Channel ID to get views for
    
    Returns:
        {
            "success": true,
            "channel_id": "username",
            "current_views": {
                "total_views": 17711331,
                "gif_views": {"gif1": 1000, "gif2": 2000, ...},
                "timestamp": "2024-12-27T15:30:00",
                "fetched_count": 9
            },
            "previous_views": {
                "total_views": 17000000,
                "timestamp": "2024-12-27T14:30:00"
            },
            "comparison": {
                "current_total": 17711331,
                "previous_total": 17000000,
                "difference": +711331,
                "status": "increasing" | "decreasing" | "stagnant" | "no_previous"
            },
            "realtime": true,
            "note": "Fetched in real-time from Giphy API. Previous comparison from lightweight cache."
        }
    """
    channel_id = request.args.get('channel_id')
    
    if not channel_id:
        return jsonify({'error': 'channel_id is required'}), 400
    
    try:
        # Get GIF IDs for this channel
        gif_ids = []
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT gif_id FROM gifs WHERE channel_id = ?', (channel_id,))
        gif_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if not gif_ids:
            return jsonify({
                'success': False,
                'error': f'No GIFs found for channel {channel_id}. Check channel first.'
            }), 404
        
        # Get real-time comparison
        result = get_realtime_channel_views_comparison(channel_id, gif_ids)
        result['success'] = True
        result['channel_id'] = channel_id
        result['note'] = "Fetched in real-time from Giphy API. Previous comparison from lightweight JSON cache (no database storage)."
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/check-channel', methods=['POST'])
def check_channel():
    """
    API endpoint to check channel status.
    Takes a Giphy channel URL, extracts the channel identifier,
    and fetches all data using the Giphy API key.
    """
    data = request.json
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    if 'giphy.com' not in url.lower():
        return jsonify({'error': 'Please provide a valid Giphy URL'}), 400
    
    # Extract channel identifier from the URL
    channel_identifier = extract_channel_info_from_url(url)
    
    if not channel_identifier:
        return jsonify({'error': 'Could not extract channel information from URL. Please check the URL format.'}), 400
    
    # Check channel status using API (all data comes from Giphy API)
    results = check_channel_status(channel_identifier, original_url=url)
    
    # ALWAYS use view-based analysis for accurate results
    # The analyze_channel_status function is already called in check_channel_status
    # with auto_check_views=True, so it will automatically scrape views if needed
    # and use strict 2-day analysis
    
    # Option: Multi-location check (most accurate, but slower)
    use_location_check = data.get('use_location_check', False)
    if use_location_check and results.get('exists') and results.get('details', {}).get('all_gifs'):
        print(f"\n{'='*60}")
        print(f"PERFORMING MULTI-LOCATION VIEW ANALYSIS (Most Accurate)")
        print(f"{'='*60}")
        
        try:
            location_analysis = analyze_channel_status_with_location_checks(channel_identifier, days=2)
            
            # OVERRIDE results with location-based analysis (most accurate)
            results.update({
                'status': location_analysis['status'],
                'working': location_analysis.get('working', False),
                'shadow_banned': location_analysis.get('shadow_banned', False),
                'banned': location_analysis.get('banned', False),
                'analysis_reasons': [location_analysis.get('reason', '')],
                'location_analysis': location_analysis.get('stats', {}),
                'analysis_method': 'multi_location_view_check_accurate'
            })
            
            # Also update details for frontend display
            if location_analysis.get('reason'):
                results['details']['analysis_reasons'] = [location_analysis.get('reason')]
            
            print(f"Location-based analysis complete: {location_analysis['status']}")
        except Exception as e:
            print(f"Error in location-based analysis: {str(e)}")
            results['location_analysis_error'] = str(e)
            # Don't change status on error - keep original analysis
    
    # Optionally update view counts in background (non-blocking)
    update_views = data.get('update_views', False)
    if update_views and results.get('exists') and results.get('details', {}).get('all_gifs'):
        # Start background thread to update views
        def update_views_background():
            try:
                gif_ids = [gif.get('id') for gif in results['details']['all_gifs'] if gif.get('id')]
                if gif_ids:
                    print(f"  Updating views for {len(gif_ids)} GIFs in background...")
                    update_gif_views_batch(gif_ids, max_workers=3)
                    print(f"  ✓ Completed view updates")
            except Exception as e:
                print(f"  ✗ Error updating views in background: {str(e)}")
        
        thread = threading.Thread(target=update_views_background)
        thread.daemon = True
        thread.start()
    
    # Add URL info to results for reference
    results['source_url'] = url
    results['channel_identifier_from_url'] = channel_identifier
    
    # Ensure channel_id is always set even if channel not found
    if not results.get('channel_id'):
        results['channel_id'] = channel_identifier
    
    # Debug: Print results to console
    print(f"\n=== Channel Check Results ===")
    print(f"Channel ID: {channel_identifier}")
    print(f"Exists: {results.get('exists')}")
    print(f"Status: {results.get('status')}")
    print(f"GIFs found: {len(results.get('details', {}).get('all_gifs', []))}")
    print(f"Method: {results.get('method')}")
    if results.get('error'):
        print(f"Error: {results.get('error')}")
    print("=" * 30 + "\n")
    
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True, port=5000)

