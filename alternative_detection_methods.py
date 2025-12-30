"""
Alternative Methods for Detecting Shadow Banned vs Working Channels

These methods can supplement or replace view-based detection:
"""

import requests
from datetime import datetime, timedelta
import re

# These will be set from main app
GIPHY_API_BASE = 'https://api.giphy.com/v1'
GIPHY_API_KEY = None

def set_api_config(api_base, api_key):
    """Set API configuration from main app"""
    global GIPHY_API_BASE, GIPHY_API_KEY
    GIPHY_API_BASE = api_base
    GIPHY_API_KEY = api_key

def check_gif_search_visibility(gif_id, gif_title, channel_username):
    """
    ALTERNATIVE METHOD 1: Search Visibility Test
    Check if GIFs from channel appear in general search results (not just username search)
    
    Logic:
    - Extract keywords from GIF titles
    - Search Giphy with those keywords
    - If GIF appears in results → WORKING (visible in search)
    - If GIF doesn't appear → SHADOW BANNED (suppressed from search)
    
    This is a strong indicator because shadow-banned channels have reduced search visibility.
    """
    try:
        # Extract keywords from title (remove common words)
        import re
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        words = re.findall(r'\w+', gif_title.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 3][:3]  # Top 3 keywords
        
        if not keywords:
            return None  # Can't test without keywords
        
        # Search with keywords
        search_query = ' '.join(keywords)
        search_url = f"{GIPHY_API_BASE}/gifs/search"
        search_params = {
            'api_key': GIPHY_API_KEY,
            'q': search_query,
            'limit': 25
        }
        
        response = requests.get(search_url, params=search_params, timeout=10)
        if response.status_code == 200:
            results = response.json().get('data', [])
            
            # Check if our GIF appears in results
            for gif in results:
                if gif.get('id') == gif_id:
                    return {
                        'visible_in_search': True,
                        'position': results.index(gif) + 1,
                        'search_query': search_query,
                        'total_results': len(results)
                    }
            
            return {
                'visible_in_search': False,
                'search_query': search_query,
                'total_results': len(results),
                'note': 'GIF not found in search results'
            }
    except Exception as e:
        return {'error': str(e)}
    
    return None


def check_recent_upload_activity(all_gifs_list):
    """
    ALTERNATIVE METHOD 2: Recent Upload Activity Analysis
    Analyze upload dates to determine channel activity
    
    Logic:
    - Check dates of recent uploads
    - If uploads are recent (within last 30 days) → WORKING (active channel)
    - If all uploads are old (6+ months) → SHADOW BANNED (inactive/abandoned)
    - Calculate upload frequency
    """
    from datetime import datetime, timedelta
    
    try:
        if not all_gifs_list:
            return None
        
        recent_uploads = 0
        old_uploads = 0
        upload_dates = []
        
        for gif in all_gifs_list:
            import_datetime = gif.get('import_datetime', '')
            if import_datetime:
                try:
                    # Parse date (format: "2023-12-01 12:00:00")
                    upload_date = datetime.strptime(import_datetime, '%Y-%m-%d %H:%M:%S')
                    upload_dates.append(upload_date)
                    
                    days_ago = (datetime.now() - upload_date).days
                    
                    if days_ago <= 30:
                        recent_uploads += 1
                    elif days_ago > 180:  # 6+ months
                        old_uploads += 1
                except:
                    pass
        
        if not upload_dates:
            return None
        
        # Calculate metrics
        total_checked = len(upload_dates)
        recent_percentage = (recent_uploads / total_checked * 100) if total_checked > 0 else 0
        
        # Find most recent upload
        most_recent = max(upload_dates) if upload_dates else None
        days_since_last_upload = (datetime.now() - most_recent).days if most_recent else None
        
        # Determine status
        if recent_percentage >= 30:  # 30%+ recent uploads
            activity_status = 'active'
        elif days_since_last_upload and days_since_last_upload <= 30:
            activity_status = 'active'
        elif days_since_last_upload and days_since_last_upload > 180:
            activity_status = 'inactive'
        else:
            activity_status = 'moderate'
        
        return {
            'recent_uploads': recent_uploads,
            'old_uploads': old_uploads,
            'total_checked': total_checked,
            'recent_percentage': recent_percentage,
            'most_recent_upload': most_recent.isoformat() if most_recent else None,
            'days_since_last_upload': days_since_last_upload,
            'activity_status': activity_status
        }
    except Exception as e:
        return {'error': str(e)}
    
    return None


def check_trending_status(gif_ids):
    """
    ALTERNATIVE METHOD 3: Trending/Featured Detection
    Check if any GIFs from channel are trending or featured
    
    Logic:
    - Fetch trending GIFs
    - Check if channel's GIFs appear in trending
    - If any GIFs are trending → WORKING (high engagement)
    - If no GIFs trending → Check other indicators
    """
    try:
        trending_url = f"{GIPHY_API_BASE}/gifs/trending"
        trending_params = {
            'api_key': GIPHY_API_KEY,
            'limit': 50
        }
        
        response = requests.get(trending_url, params=trending_params, timeout=10)
        if response.status_code == 200:
            trending_gifs = response.json().get('data', [])
            trending_gif_ids = [gif.get('id') for gif in trending_gifs]
            
            # Check if any channel GIFs are trending
            channel_trending = [gid for gid in gif_ids if gid in trending_gif_ids]
            
            return {
                'has_trending_gifs': len(channel_trending) > 0,
                'trending_count': len(channel_trending),
                'trending_gif_ids': channel_trending,
                'total_trending_checked': len(trending_gif_ids)
            }
    except Exception as e:
        return {'error': str(e)}
    
    return None


def check_general_search_appearance(channel_username, sample_gif_ids):
    """
    ALTERNATIVE METHOD 4: General Search Appearance (without username filter)
    Search for channel's GIFs using generic queries (title keywords)
    
    Logic:
    - Take sample GIFs from channel
    - Search using their titles/keywords (without username filter)
    - Count how many appear in search results
    - High appearance rate → WORKING
    - Low/zero appearance → SHADOW BANNED
    """
    try:
        visible_count = 0
        total_tested = min(5, len(sample_gif_ids))  # Test first 5 GIFs
        
        for gif_id in sample_gif_ids[:total_tested]:
            # Get GIF details
            gif_detail_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
            gif_detail_params = {'api_key': GIPHY_API_KEY}
            gif_response = requests.get(gif_detail_url, params=gif_detail_params, timeout=5)
            
            if gif_response.status_code == 200:
                gif_data = gif_response.json().get('data', {})
                title = gif_data.get('title', '')
                
                if title:
                    # Search with title keywords
                    visibility_result = check_gif_search_visibility(gif_id, title, channel_username)
                    if visibility_result and visibility_result.get('visible_in_search'):
                        visible_count += 1
        
        visibility_rate = (visible_count / total_tested * 100) if total_tested > 0 else 0
        
        return {
            'visible_count': visible_count,
            'total_tested': total_tested,
            'visibility_rate': visibility_rate,
            'status': 'working' if visibility_rate >= 40 else ('shadow_banned' if visibility_rate == 0 else 'uncertain')
        }
    except Exception as e:
        return {'error': str(e)}
    
    return None


def comprehensive_alternative_analysis(channel_id, all_gifs_list, gif_ids):
    """
    COMPREHENSIVE ALTERNATIVE ANALYSIS
    Combine multiple alternative methods for better detection
    """
    results = {
        'search_visibility': None,
        'recent_activity': None,
        'trending_status': None,
        'general_search': None,
        'composite_score': 0,
        'alternative_status': 'unknown'
    }
    
    # Method 1: Recent Upload Activity
    recent_activity = check_recent_upload_activity(all_gifs_list)
    results['recent_activity'] = recent_activity
    
    if recent_activity and recent_activity.get('activity_status') == 'active':
        results['composite_score'] += 30  # Active channels are likely working
    
    # Method 2: Trending Status
    trending_status = check_trending_status(gif_ids[:20])  # Check first 20 GIFs
    results['trending_status'] = trending_status
    
    if trending_status and trending_status.get('has_trending_gifs'):
        results['composite_score'] += 25  # Trending = high engagement
    
    # Method 3: General Search Visibility (sample test)
    if len(gif_ids) > 0:
        search_visibility = check_general_search_appearance(channel_id, gif_ids)
        results['general_search'] = search_visibility
        
        if search_visibility:
            visibility_rate = search_visibility.get('visibility_rate', 0)
            if visibility_rate >= 40:
                results['composite_score'] += 25  # Good search visibility
            elif visibility_rate == 0:
                results['composite_score'] -= 20  # No search visibility = shadow banned
    
    # Method 4: Upload Count (already checked in main logic)
    # Many uploads = likely working (already handled)
    
    # Determine alternative status based on composite score
    if results['composite_score'] >= 50:
        results['alternative_status'] = 'working'
    elif results['composite_score'] <= 0:
        results['alternative_status'] = 'shadow_banned'
    else:
        results['alternative_status'] = 'uncertain'
    
    return results


# INTEGRATION EXAMPLE:
# You can integrate these methods into analyze_channel_status() function
# to provide additional indicators when view data is unavailable

