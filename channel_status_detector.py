"""
Channel Status Detector
Detects if a GIPHY channel is banned, shadow banned, or working properly.

Detection Logic:
1. Banned: Channel not found in API, or no GIFs/views returned
2. Shadow Banned: GIF count and views shown, but GIFs not found in search results
3. Working: Channel exists, has GIFs/views, and GIFs appear in search results
"""

import requests
import os
import time
import re
from typing import Dict, List, Optional, Tuple

# GIPHY API configuration
GIPHY_API_KEY = os.environ.get('GIPHY_API_KEY', 'L8eXbxrbPETZxlvgXN9kIEzQ55Df04v0')
GIPHY_API_BASE = 'https://api.giphy.com/v1'

# Request timeout and delay settings
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1  # Delay between API requests in seconds


def fetch_channel_info(channel_username: str) -> Dict:
    """
    Fetch channel information including total GIF count and views from GIPHY API.
    
    Args:
        channel_username: The channel username to check
        
    Returns:
        Dictionary with channel info, total_gifs, total_views, and status
        {
            'exists': bool,
            'total_gifs': int,
            'total_views': int,
            'user_data': dict or None,
            'gifs_list': list or None,
            'error': str or None
        }
    """
    result = {
        'exists': False,
        'total_gifs': 0,
        'total_views': 0,
        'user_data': None,
        'gifs_list': [],
        'error': None
    }
    
    try:
        # Step 1: Try to fetch GIFs by username first (more reliable than user endpoint)
        # Many channels exist but aren't accessible via /users endpoint
        gifs_url = f"{GIPHY_API_BASE}/gifs/search"
        all_gifs = []
        offset = 0
        limit = 50  # Maximum per request
        max_pages = 20  # Fetch up to 1000 GIFs
        
        print(f"Fetching GIFs for channel: {channel_username}")
        
        # Try different case variants (API usernames are case-sensitive)
        # e.g., "bloomscroll" URL might need "Bloomscroll" in API
        username_variants = [
            channel_username,  # Try as-is first
            channel_username.capitalize(),  # Try capitalized (e.g., "bloomscroll" -> "Bloomscroll")
            channel_username.title(),  # Try title case
        ]
        # Remove duplicates while preserving order
        username_variants = list(dict.fromkeys(username_variants))
        
        username_found = None
        for username_variant in username_variants:
            # Try Method 1: Search with username parameter
            offset = 0
            variant_gifs = []
            
            for page in range(max_pages):
                gifs_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': '',  # Empty query to get all from user
                    'username': username_variant,
                    'limit': limit,
                    'offset': offset
                }
                
                gifs_response = requests.get(gifs_url, params=gifs_params, timeout=REQUEST_TIMEOUT)
                
                if gifs_response.status_code == 200:
                    gifs_data = gifs_response.json().get('data', [])
                    if not gifs_data:
                        break
                    
                    variant_gifs.extend(gifs_data)
                    result['exists'] = True  # If we got GIFs, channel exists
                    
                    # If we got fewer than limit, we've reached the end
                    if len(gifs_data) < limit:
                        break
                    
                    offset += limit
                    time.sleep(REQUEST_DELAY)  # Rate limiting
                elif gifs_response.status_code == 404:
                    break  # No GIFs found with this username variant
                else:
                    break  # Some error, try next variant
            
            # If we found GIFs with this variant, use it
            if len(variant_gifs) > 0:
                username_found = username_variant
                all_gifs = variant_gifs
                print(f"  Found {len(all_gifs)} GIFs with username: {username_variant}")
                
                # Extract user info from first GIF if available
                if not result['user_data']:
                    first_gif = all_gifs[0]
                    user_from_gif = first_gif.get('user')
                    if user_from_gif:
                        result['user_data'] = user_from_gif
                        # Use the actual username from API response
                        actual_username = user_from_gif.get('username', username_variant)
                        print(f"[OK] Channel exists: Found {len(all_gifs)} GIFs (username: {actual_username})")
                break  # Found GIFs, no need to try other variants
        
        # Method 2: If username parameter didn't work, try searching by channel name
        # and filter results by username
        if len(all_gifs) == 0:
            print(f"  Username parameter didn't work, trying search query method...")
            offset = 0
            
            for page in range(max_pages):
                gifs_params = {
                    'api_key': GIPHY_API_KEY,
                    'q': channel_username,  # Use channel name as search query
                    'limit': limit,
                    'offset': offset
                }
                
                gifs_response = requests.get(gifs_url, params=gifs_params, timeout=REQUEST_TIMEOUT)
                
                if gifs_response.status_code == 200:
                    gifs_data = gifs_response.json().get('data', [])
                    if not gifs_data:
                        break
                    
                    # Filter GIFs by username (case-insensitive)
                    channel_username_lower = channel_username.lower()
                    filtered_gifs = []
                    for gif in gifs_data:
                        gif_user = gif.get('user', {})
                        gif_username = gif_user.get('username', '').lower() if gif_user.get('username') else ''
                        # Also check display_name as fallback
                        gif_display_name = gif_user.get('display_name', '').lower() if gif_user.get('display_name') else ''
                        
                        if (gif_username == channel_username_lower or 
                            gif_display_name == channel_username_lower or
                            channel_username_lower in gif_username or
                            channel_username_lower in gif_display_name):
                            filtered_gifs.append(gif)
                    
                    if len(filtered_gifs) > 0:
                        all_gifs.extend(filtered_gifs)
                        print(f"  Fetched {len(filtered_gifs)} GIFs from search (total: {len(all_gifs)})")
                        result['exists'] = True
                        
                        # Extract user info from first GIF if available
                        if not result['user_data']:
                            first_gif = filtered_gifs[0]
                            user_from_gif = first_gif.get('user')
                            if user_from_gif:
                                result['user_data'] = user_from_gif
                    elif offset == 0:
                        # No matching GIFs found even in first page
                        break
                    
                    # If we got fewer than limit, we've reached the end
                    if len(gifs_data) < limit:
                        break
                    
                    offset += limit
                    time.sleep(REQUEST_DELAY)  # Rate limiting
                else:
                    break
        
        # Step 2: If we found GIFs, try to get user info from user endpoint (optional, for additional info)
        if result['exists'] and len(all_gifs) > 0 and not result['user_data']:
            try:
                user_url = f"{GIPHY_API_BASE}/users/{channel_username}"
                user_params = {'api_key': GIPHY_API_KEY}
                user_response = requests.get(user_url, params=user_params, timeout=REQUEST_TIMEOUT)
                
                if user_response.status_code == 200:
                    user_data = user_response.json().get('data', {})
                    result['user_data'] = user_data
                    print(f"[OK] User info found: {user_data.get('display_name', channel_username)}")
            except:
                pass  # User endpoint not available, but we have GIFs so channel exists
        
        # If no GIFs found, channel doesn't exist or is inaccessible
        if not result['exists']:
            result['error'] = 'Channel not found - no GIFs accessible via API'
            print(f"[X] Channel not found: {channel_username}")
            return result
        
        result['gifs_list'] = all_gifs
        result['total_gifs'] = len(all_gifs)
        
        # Step 3: Calculate total views from all GIFs
        total_views = 0
        for gif in all_gifs:
            # Get views from analytics if available
            analytics = gif.get('analytics', {})
            onload = analytics.get('onload', {})
            view_count = onload.get('count', 0)
            total_views += view_count
        
        result['total_views'] = total_views
        
        print(f"[OK] Total GIFs: {result['total_gifs']}, Total Views: {result['total_views']}")
        
    except requests.exceptions.RequestException as e:
        result['error'] = f'Request error: {str(e)}'
        print(f"[X] Request error: {str(e)}")
    except Exception as e:
        result['error'] = f'Unexpected error: {str(e)}'
        print(f"[X] Unexpected error: {str(e)}")
    
    return result


def check_banned_channel(channel_username: str) -> Dict:
    """
    Check if a channel is banned.
    
    Logic: If channel is not found in API, or no GIFs/views are returned,
    then the channel is considered banned.
    
    Args:
        channel_username: The channel username to check
        
    Returns:
        Dictionary with banned status and details
        {
            'is_banned': bool,
            'reason': str,
            'channel_info': dict or None
        }
    """
    result = {
        'is_banned': False,
        'reason': None,
        'channel_info': None
    }
    
    print(f"\n{'='*60}")
    print(f"Checking BANNED status for: {channel_username}")
    print(f"{'='*60}")
    
    # Fetch channel info
    channel_info = fetch_channel_info(channel_username)
    result['channel_info'] = channel_info
    
    # Check if channel exists
    if not channel_info.get('exists'):
        result['is_banned'] = True
        result['reason'] = 'Channel not found in GIPHY API'
        print(f"[X] BANNED: Channel not found")
        return result
    
    # Check if channel has any GIFs
    total_gifs = channel_info.get('total_gifs', 0)
    if total_gifs == 0:
        result['is_banned'] = True
        result['reason'] = 'Channel exists but has no GIFs (possible ban)'
        print(f"[X] BANNED: Channel has no GIFs")
        return result
    
    # Check if there's an error that might indicate a ban
    if channel_info.get('error'):
        error_msg = channel_info.get('error', '')
        if '404' in error_msg or '403' in error_msg or 'forbidden' in error_msg.lower():
            result['is_banned'] = True
            result['reason'] = f'API error indicates ban: {error_msg}'
            print(f"[X] BANNED: {result['reason']}")
            return result
    
    # If we got here, channel exists and has GIFs - not banned
    print(f"[OK] NOT BANNED: Channel exists with {total_gifs} GIFs")
    result['is_banned'] = False
    result['reason'] = 'Channel exists and has content'
    
    return result


def get_gif_tags(gif_id: str) -> List[str]:
    """
    Get tags for a specific GIF.
    
    Args:
        gif_id: The GIF ID
        
    Returns:
        List of tags associated with the GIF
    """
    try:
        gif_url = f"{GIPHY_API_BASE}/gifs/{gif_id}"
        gif_params = {'api_key': GIPHY_API_KEY}
        
        response = requests.get(gif_url, params=gif_params, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 200:
            gif_data = response.json().get('data', {})
            tags = gif_data.get('tags', [])
            return tags if tags else []
        else:
            return []
    except Exception as e:
        print(f"  Error fetching tags for GIF {gif_id}: {str(e)}")
        return []


def check_gif_in_search_results(gif_id: str, search_query: str, max_results: int = 2500, sort_type: str = 'relevant') -> bool:
    """
    Check if a GIF appears in search results for a given query.
    Matches JavaScript logic: searches up to 2500 results using limit=100.
    Supports both 'relevant' and 'newest' sort types.
    
    Args:
        gif_id: The GIF ID to search for
        search_query: The search query/tag to use
        max_results: Maximum number of results to check (default 2500, checks multiple pages)
        sort_type: Sort type - 'relevant' (default) or 'newest' (uses sort=recent)
        
    Returns:
        True if GIF is found in search results, False otherwise
    """
    try:
        search_url = f"{GIPHY_API_BASE}/gifs/search"
        limit = 100  # Use larger batches like the JavaScript code
        offset = 0
        max_offset = max_results - limit  # e.g., 2400 for 2500 total results
        
        while offset <= max_offset:
            search_params = {
                'api_key': GIPHY_API_KEY,
                'q': search_query,
                'limit': limit,
                'offset': offset
            }
            
            # Add sort parameter for 'newest' (recent)
            if sort_type == 'newest':
                search_params['sort'] = 'recent'
            
            response = requests.get(search_url, params=search_params, timeout=REQUEST_TIMEOUT)
            
            if response.status_code == 200:
                response_data = response.json()
                search_results = response_data.get('data', [])
                pagination = response_data.get('pagination', {})
                total_count = pagination.get('total_count', 0)
                
                if not search_results:
                    # No data returned, but continue searching if we haven't reached maxOffset
                    if offset < max_offset:
                        offset += limit
                        continue
                    else:
                        break
                
                # Check if our GIF is in the results
                for gif in search_results:
                    if gif.get('id') == gif_id:
                        return True
                
                # Continue searching even if this batch has fewer than limit results
                # Only break if we've reached the actual end of results
                if len(search_results) < limit:
                    # Check if we've reached the end based on pagination
                    if total_count > 0 and offset + len(search_results) >= total_count:
                        # Reached end of results
                        break
                
                # Move to next page
                offset += limit
                
                # Small delay between pages to avoid rate limiting
                if offset <= max_offset:
                    time.sleep(REQUEST_DELAY)
            else:
                # If request fails, stop searching
                break
        
        return False
    except Exception as e:
        print(f"  Error checking search results: {str(e)}")
        return False


def check_shadow_banned_channel(channel_username: str, channel_gifs: List[Dict] = None) -> Dict:
    """
    Check if a channel is shadow banned.
    
    Logic: 
    - Fetch first 15 GIFs from the channel
    - Get tags for each GIF
    - Check if each GIF appears in search results for its tags
    - If GIFs are not found in search results, channel is shadow banned
    
    Args:
        channel_username: The channel username to check
        channel_gifs: Optional list of GIFs (if already fetched)
        
    Returns:
        Dictionary with shadow banned status and details
        {
            'is_shadow_banned': bool,
            'gifs_checked': int,
            'gifs_found_in_search': int,
            'gifs_not_found': int,
            'details': list of dict with results for each GIF
        }
    """
    result = {
        'is_shadow_banned': False,
        'gifs_checked': 0,
        'gifs_found_in_search': 0,
        'gifs_not_found': 0,
        'details': []
    }
    
    print(f"\n{'='*60}")
    print(f"Checking SHADOW BANNED status for: {channel_username}")
    print(f"{'='*60}")
    
    # Fetch GIFs if not provided
    if channel_gifs is None:
        channel_info = fetch_channel_info(channel_username)
        if not channel_info.get('exists'):
            result['is_shadow_banned'] = True
            result['reason'] = 'Channel not found'
            return result
        channel_gifs = channel_info.get('gifs_list', [])
    
    # Take first 15 GIFs
    sample_gifs = channel_gifs[:15]
    result['gifs_checked'] = len(sample_gifs)
    
    if len(sample_gifs) == 0:
        result['is_shadow_banned'] = True
        result['reason'] = 'No GIFs to check'
        print(f"[X] SHADOW BANNED: No GIFs available to check")
        return result
    
    print(f"Checking {len(sample_gifs)} GIFs for search visibility...")
    
    gifs_found = 0
    gifs_not_found = 0
    
    for i, gif in enumerate(sample_gifs, 1):
        gif_id = gif.get('id')
        gif_title = gif.get('title', '')
        
        print(f"\n  [{i}/{len(sample_gifs)}] Checking GIF: {gif_id}")
        
        # Get tags for this GIF
        tags = get_gif_tags(gif_id)
        
        if not tags:
            # If no tags, try using the title as search query
            if gif_title:
                tags = [word for word in gif_title.split() if len(word) > 3]
            else:
                tags = []
        
        if not tags:
            print(f"    [WARN] No tags or title available, skipping")
            result['details'].append({
                'gif_id': gif_id,
                'found': False,
                'reason': 'No tags available'
            })
            gifs_not_found += 1
            continue
        
        print(f"    Found {len(tags)} tag(s): {', '.join(tags)}")
        
        # Check if GIF appears in search results for each tag
        found_in_any_tag = False
        checked_tags = []
        
        for tag in tags:  # Check all tags
            time.sleep(REQUEST_DELAY)  # Rate limiting
            
            # Check 'relevant' sort type
            found_relevant = check_gif_in_search_results(gif_id, tag, max_results=2500, sort_type='relevant')
            
            # Check 'newest' sort type
            found_newest = False
            if not found_relevant:
                time.sleep(REQUEST_DELAY)  # Rate limiting between sort types
                found_newest = check_gif_in_search_results(gif_id, tag, max_results=2500, sort_type='newest')
            
            is_found = found_relevant or found_newest
            checked_tags.append({
                'tag': tag, 
                'found': is_found,
                'found_relevant': found_relevant,
                'found_newest': found_newest
            })
            
            if is_found:
                found_in_any_tag = True
                sort_types_found = []
                if found_relevant:
                    sort_types_found.append('relevant')
                if found_newest:
                    sort_types_found.append('newest')
                print(f"    [OK] Found in search for tag: '{tag}' (sort types: {', '.join(sort_types_found)})")
                break
            else:
                print(f"    [X] Not found in search for tag: '{tag}' (checked both 'relevant' and 'newest')")
        
        if found_in_any_tag:
            gifs_found += 1
            result['details'].append({
                'gif_id': gif_id,
                'found': True,
                'checked_tags': checked_tags
            })
        else:
            gifs_not_found += 1
            result['details'].append({
                'gif_id': gif_id,
                'found': False,
                'checked_tags': checked_tags
            })
    
    result['gifs_found_in_search'] = gifs_found
    result['gifs_not_found'] = gifs_not_found
    
    # Determine if shadow banned
    # If less than 30% of GIFs are found in search, consider it shadow banned
    visibility_rate = (gifs_found / result['gifs_checked'] * 100) if result['gifs_checked'] > 0 else 0
    
    if visibility_rate < 30:
        result['is_shadow_banned'] = True
        result['reason'] = f'Only {visibility_rate:.1f}% of GIFs visible in search'
        print(f"\n[X] SHADOW BANNED: Only {gifs_found}/{result['gifs_checked']} GIFs found in search ({visibility_rate:.1f}%)")
    else:
        result['is_shadow_banned'] = False
        result['reason'] = f'{visibility_rate:.1f}% of GIFs visible in search'
        print(f"\n[OK] NOT SHADOW BANNED: {gifs_found}/{result['gifs_checked']} GIFs found in search ({visibility_rate:.1f}%)")
    
    return result


def check_working_channel(channel_username: str, channel_gifs: List[Dict] = None) -> Dict:
    """
    Check if a channel is working properly.
    
    Logic:
    - Channel exists and has GIFs/views (not banned)
    - GIFs from the channel appear in search results (not shadow banned)
    - Count how many GIFs from the same channel URL are found in search results
    
    Args:
        channel_username: The channel username to check
        channel_gifs: Optional list of GIFs (if already fetched)
        
    Returns:
        Dictionary with working status and details
        {
            'is_working': bool,
            'gifs_found_in_search': int,
            'total_gifs_checked': int,
            'visibility_rate': float,
            'details': dict with banned and shadow banned results
        }
    """
    result = {
        'is_working': False,
        'gifs_found_in_search': 0,
        'total_gifs_checked': 0,
        'visibility_rate': 0.0,
        'details': {}
    }
    
    print(f"\n{'='*60}")
    print(f"Checking WORKING status for: {channel_username}")
    print(f"{'='*60}")
    
    # Step 1: Check if banned
    banned_result = check_banned_channel(channel_username)
    result['details']['banned_check'] = banned_result
    
    if banned_result.get('is_banned'):
        result['is_working'] = False
        result['reason'] = 'Channel is banned'
        print(f"[X] NOT WORKING: Channel is banned")
        return result
    
    # Step 2: Check if shadow banned (this will also verify GIFs in search)
    if channel_gifs is None:
        channel_info = banned_result.get('channel_info', {})
        channel_gifs = channel_info.get('gifs_list', [])
    
    shadow_banned_result = check_shadow_banned_channel(channel_username, channel_gifs)
    result['details']['shadow_banned_check'] = shadow_banned_result
    
    if shadow_banned_result.get('is_shadow_banned'):
        result['is_working'] = False
        result['reason'] = 'Channel is shadow banned'
        print(f"[X] NOT WORKING: Channel is shadow banned")
        return result
    
    # Step 3: If not banned and not shadow banned, channel is working
    result['is_working'] = True
    result['gifs_found_in_search'] = shadow_banned_result.get('gifs_found_in_search', 0)
    result['total_gifs_checked'] = shadow_banned_result.get('gifs_checked', 0)
    
    if result['total_gifs_checked'] > 0:
        result['visibility_rate'] = (result['gifs_found_in_search'] / result['total_gifs_checked']) * 100
    else:
        result['visibility_rate'] = 0.0
    
    result['reason'] = f'Channel is working - {result["gifs_found_in_search"]}/{result["total_gifs_checked"]} GIFs visible in search'
    print(f"[OK] WORKING: Channel is active and {result['gifs_found_in_search']}/{result['total_gifs_checked']} GIFs are visible in search ({result['visibility_rate']:.1f}%)")
    
    return result


def detect_channel_status(channel_input: str) -> Dict:
    """
    Main function to detect channel status (banned, shadow banned, or working).
    
    This function orchestrates all detection methods and returns a comprehensive result.
    Can accept either a GIPHY channel URL or a channel username.
    
    Args:
        channel_input: The channel URL (e.g., https://giphy.com/channel/Brunch-us) or username (e.g., Brunch-us)
        
    Returns:
        Dictionary with complete channel status analysis
        {
            'channel_username': str,
            'status': 'banned' | 'shadow_banned' | 'working' | 'unknown',
            'banned_check': dict,
            'shadow_banned_check': dict,
            'working_check': dict,
            'summary': dict with key metrics
        }
    """
    # Extract username from URL if it's a URL
    channel_username = extract_channel_username_from_url(channel_input)
    
    if not channel_username:
        return {
            'channel_username': None,
            'status': 'error',
            'error': f'Could not extract channel username from: {channel_input}',
            'summary': {
                'status': 'ERROR',
                'reason': f'Invalid input: {channel_input}'
            }
        }
    
    print(f"\n{'='*70}")
    print(f"CHANNEL STATUS DETECTION")
    print(f"Input: {channel_input}")
    print(f"Channel: {channel_username}")
    print(f"{'='*70}\n")
    
    result = {
        'channel_username': channel_username,
        'original_input': channel_input,
        'status': 'unknown',
        'banned_check': None,
        'shadow_banned_check': None,
        'working_check': None,
        'summary': {}
    }
    
    try:
        # Step 1: Check if banned
        banned_result = check_banned_channel(channel_username)
        result['banned_check'] = banned_result
        
        if banned_result.get('is_banned'):
            result['status'] = 'banned'
            result['summary'] = {
                'status': 'BANNED',
                'reason': banned_result.get('reason', 'Channel not found'),
                'total_gifs': 0,
                'total_views': 0
            }
            return result
        
        # Step 2: Get channel info for shadow banned check
        channel_info = banned_result.get('channel_info', {})
        channel_gifs = channel_info.get('gifs_list', [])
        total_gifs = channel_info.get('total_gifs', 0)
        total_views = channel_info.get('total_views', 0)
        
        # Step 3: Check if shadow banned
        shadow_banned_result = check_shadow_banned_channel(channel_username, channel_gifs)
        result['shadow_banned_check'] = shadow_banned_result
        
        if shadow_banned_result.get('is_shadow_banned'):
            result['status'] = 'shadow_banned'
            result['summary'] = {
                'status': 'SHADOW BANNED',
                'reason': shadow_banned_result.get('reason', 'GIFs not visible in search'),
                'total_gifs': total_gifs,
                'total_views': total_views,
                'gifs_checked': shadow_banned_result.get('gifs_checked', 0),
                'gifs_found_in_search': shadow_banned_result.get('gifs_found_in_search', 0)
            }
            return result
        
        # Step 4: Check if working (not banned and not shadow banned)
        working_result = check_working_channel(channel_username, channel_gifs)
        result['working_check'] = working_result
        
        if working_result.get('is_working'):
            result['status'] = 'working'
            result['summary'] = {
                'status': 'WORKING',
                'reason': working_result.get('reason', 'Channel is active'),
                'total_gifs': total_gifs,
                'total_views': total_views,
                'gifs_checked': working_result.get('total_gifs_checked', 0),
                'gifs_found_in_search': working_result.get('gifs_found_in_search', 0),
                'visibility_rate': working_result.get('visibility_rate', 0.0)
            }
        else:
            result['status'] = 'unknown'
            result['summary'] = {
                'status': 'UNKNOWN',
                'reason': 'Could not determine status',
                'total_gifs': total_gifs,
                'total_views': total_views
            }
        
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
        result['summary'] = {
            'status': 'ERROR',
            'reason': f'Error during detection: {str(e)}'
        }
        print(f"[X] Error during detection: {str(e)}")
    
    # Print final summary
    print(f"\n{'='*70}")
    print(f"FINAL STATUS: {result['summary'].get('status', 'UNKNOWN')}")
    print(f"{'='*70}")
    print(f"Channel: {channel_username}")
    print(f"Status: {result['status']}")
    if result['summary'].get('total_gifs') is not None:
        print(f"Total GIFs: {result['summary'].get('total_gifs', 0)}")
    if result['summary'].get('total_views') is not None:
        print(f"Total Views: {result['summary'].get('total_views', 0)}")
    if result['summary'].get('visibility_rate') is not None:
        print(f"Visibility Rate: {result['summary'].get('visibility_rate', 0):.1f}%")
    print(f"Reason: {result['summary'].get('reason', 'N/A')}")
    print(f"{'='*70}\n")
    
    return result


def extract_channel_username_from_url(url: str) -> Optional[str]:
    """
    Extract channel username or ID from Giphy URL.
    
    Supports multiple URL formats:
    - https://giphy.com/channel/username
    - https://giphy.com/@username
    - https://giphy.com/username
    - Just "username" (returns as-is)
    
    Args:
        url: The GIPHY channel URL or username
        
    Returns:
        Extracted username/ID or None if extraction fails
    """
    if not url or not url.strip():
        return None
    
    url_original = url.strip()
    
    # If it doesn't look like a URL (no http/https or giphy.com), return as-is (assume it's already a username)
    if not ('http' in url_original.lower() or 'giphy.com' in url_original.lower()):
        return url_original
    
    # Clean the URL - remove protocol, www, trailing slashes
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
        parts = gif_path.split('-')
        if len(parts) > 1:
            potential_username = parts[0]
            skip_words = ['gifs', 'gif', 'stickers', 'clips']
            if potential_username.lower() not in skip_words:
                # Extract from original URL to preserve case
                orig_match = re.search(r'giphy\.com/gifs/([^/]+)', url, re.IGNORECASE)
                if orig_match:
                    orig_parts = orig_match.group(1).split('-')
                    if len(orig_parts) > 1:
                        return orig_parts[0]
                return potential_username
    
    # Patterns for channel URLs
    patterns = [
        r'giphy\.com/channel/([^/?]+)',  # /channel/username (e.g., https://giphy.com/channel/Brunch-us)
        r'giphy\.com/@([^/?]+)',          # /@username (e.g., https://giphy.com/@Brunch-us)
        r'giphy\.com/([^/?]+)/channel',   # /username/channel (reverse format)
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


# Example usage
if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        input_value = sys.argv[1]
    else:
        # Default example - Brunch channel from the image
        input_value = 'https://giphy.com/opendroids_'
    
    # detect_channel_status will handle URL extraction
    result = detect_channel_status(input_value)
    
    # Check for errors
    if result.get('status') == 'error':
        print(f"Error: {result.get('error', 'Unknown error')}")
        print("Please provide either:")
        print("  - A GIPHY channel URL (e.g., https://giphy.com/channel/Brunch-us)")
        print("  - A channel username (e.g., Brunch-us)")
        sys.exit(1)
    
    # Print JSON result for programmatic access
    import json
    print("\n" + "="*70)
    print("JSON RESULT:")
    print("="*70)
    print(json.dumps(result, indent=2, default=str))

