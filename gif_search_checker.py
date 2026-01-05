"""
GIF Search Visibility Checker
Checks if a specific GIF appears in GIPHY search results for each of its tags.
Matches JavaScript getCurrentPosition logic exactly.
"""

import requests
import time
import os
from typing import Dict, List, Optional

# GIPHY API configuration
GIPHY_API_KEY = os.environ.get('GIPHY_API_KEY', 'L8eXbxrbPETZxlvgXN9kIEzQ55Df04v0')
GIPHY_API_BASE = 'https://api.giphy.com/v1'

# Request settings
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1  # Delay between API requests in seconds


def get_gif_tags(gif_id: str) -> List[str]:
    """
    Get tags for a specific GIF from GIPHY API.
    
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
            tags_raw = gif_data.get('tags', []) or []
            
            # Handle tags - they might be strings or objects
            tags = []
            if tags_raw:
                for tag_item in tags_raw:
                    if isinstance(tag_item, str):
                        tag_clean = tag_item.replace('#', '').strip()
                        if tag_clean:
                            tags.append(tag_clean)
                    elif isinstance(tag_item, dict):
                        tag_text = tag_item.get('text', tag_item.get('name', ''))
                        if tag_text:
                            tag_clean = str(tag_text).replace('#', '').strip()
                            if tag_clean:
                                tags.append(tag_clean)
            
            return tags
        else:
            return []
    except Exception as e:
        print(f"  Error fetching tags for GIF {gif_id}: {str(e)}")
        return []


def check_gif_in_search_for_tag(gif_id: str, tag: str, max_results: int = 2500) -> Dict:
    """
    Check if a GIF appears in search results for a specific tag.
    Matches JavaScript getCurrentPosition logic exactly.
    
    Args:
        gif_id: The GIF ID to search for
        tag: The tag/search query to check
        max_results: Maximum number of results to check (default 2500)
        
    Returns:
        Dictionary with:
        {
            'found': bool,
            'position': int or None,
            'total_results': int,
            'searched_count': int,
            'tag': str,
            'error': str or None
        }
    """
    try:
        search_url = f"{GIPHY_API_BASE}/gifs/search"
        limit = 100  # API limit per request (matches JavaScript)
        offset = 0
        max_offset = 2400  # Search up to 2500 results (2400 + 100) - matches JavaScript
        
        found_position = None
        total_results = 0
        searched_count = 0
        pages_searched = 0
        
        print(f"  🔍 Searching for GIF {gif_id} in tag '{tag}'...")
        print(f"     Will search up to {max_offset + limit} results (offset 0 to {max_offset})")
        
        while offset <= max_offset:
            # Build search params - only 'relevant' sort (default, no sort parameter)
            search_params = {
                'api_key': GIPHY_API_KEY,
                'q': tag,
                'limit': limit,
                'offset': offset
                # No sort parameter = 'relevant' (default)
            }
            
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
                
                searched_count += len(search_results)
                pages_searched += 1
                
                # Get total count from pagination if available (matches JavaScript)
                if total_count > 0:
                    total_results = total_count
                
                # Debug: Log first page info
                if pages_searched == 1:
                    print(f"     Page 1: Found {len(search_results)} results, Total available: {total_count:,}")
                
                # Check if our GIF is in the results (matches JavaScript: findIndex)
                for index, gif in enumerate(search_results):
                    result_gif_id = gif.get('id')
                    
                    # Direct comparison first (matches JavaScript: gif.id === gifId)
                    if result_gif_id == gif_id:
                        found_position = offset + index + 1
                        total_results = total_count if total_count > 0 else (offset + len(search_results))
                        print(f"    ✅ FOUND at position {found_position}")
                        return {
                            'found': True,
                            'position': found_position,
                            'total_results': total_results,
                            'searched_count': searched_count,
                            'tag': tag
                        }
                    
                    # Fallback: String comparison (handles type mismatches)
                    if str(result_gif_id).strip() == str(gif_id).strip():
                        found_position = offset + index + 1
                        total_results = total_count if total_count > 0 else (offset + len(search_results))
                        print(f"    ✅ FOUND at position {found_position}")
                        return {
                            'found': True,
                            'position': found_position,
                            'total_results': total_results,
                            'searched_count': searched_count,
                            'tag': tag
                        }
                
                # Continue searching even if this batch has fewer than limit results
                # Only break if we've reached the actual end of results
                if len(search_results) < limit:
                    # Check if we've reached the end based on pagination (matches JavaScript logic)
                    if total_count > 0 and offset + len(search_results) >= total_count:
                        # Reached end of results
                        print(f"    📊 Reached end of results at offset {offset} (total: {total_count:,})")
                        break
                
                # Move to next page
                offset += limit
                
                # Show progress every 500 results
                if offset > 0 and offset % 500 == 0:
                    print(f"    📊 Progress: Checked {offset:,} results so far (searched {searched_count:,} total)")
                
                # Check if we've reached max offset
                if offset > max_offset:
                    # Reached max offset (2500 results)
                    print(f"    📊 Reached max search limit: {max_offset + limit:,} results")
                    break
                
                # Small delay between pages to avoid rate limiting (matches JavaScript sleep)
                time.sleep(REQUEST_DELAY)
            else:
                # If request fails, stop searching
                print(f"    ❌ Request failed with status {response.status_code}")
                break
        
        # If we reach here, GIF was not found in search results
        max_searched = max_offset + limit  # 2400 + 100 = 2500
        actual_searched = min(searched_count, max_searched)
        print(f"    ❌ NOT FOUND in top {actual_searched:,} results")
        if total_results > 0 and total_results > actual_searched:
            print(f"       Tag has {total_results:,} total results - GIF might be beyond position {actual_searched:,}")
        
        return {
            'found': False,
            'position': None,
            'total_results': total_results if total_results > 0 else searched_count,
            'searched_count': searched_count,
            'tag': tag
        }
        
    except Exception as e:
        print(f"    ❌ Error checking tag '{tag}': {str(e)}")
        return {
            'found': False,
            'position': None,
            'total_results': 0,
            'searched_count': 0,
            'tag': tag,
            'error': str(e)
        }


def check_gif_tags_in_search(gif_id: str, tags: List[str]) -> Dict:
    """
    Check if a GIF appears in search results for each of its tags.
    Returns detailed results for each tag.
    
    Args:
        gif_id: The GIF ID to check
        tags: List of tags to check
        
    Returns:
        Dictionary with comprehensive results:
        {
            'gif_id': str,
            'total_tags_checked': int,
            'tags_found': int,
            'tags_not_found': int,
            'tag_results': [
                {
                    'tag': str,
                    'found': bool,
                    'position': int or None,
                    'total_results': int,
                    'searched_count': int
                },
                ...
            ],
            'found_in_any_tag': bool,
            'visibility_rate': float
        }
    """
    print(f"\n{'='*70}")
    print(f"🔍 CHECKING GIF SEARCH VISIBILITY")
    print(f"{'='*70}")
    print(f"GIF ID: {gif_id}")
    print(f"Tags to check: {len(tags)}")
    print(f"{'='*70}\n")
    
    results = {
        'gif_id': gif_id,
        'total_tags_checked': len(tags),
        'tags_found': 0,
        'tags_not_found': 0,
        'tag_results': [],
        'found_in_any_tag': False,
        'visibility_rate': 0.0
    }
    
    # Check each tag
    for i, tag in enumerate(tags, 1):
        print(f"\n[{i}/{len(tags)}] Checking tag: '{tag}'")
        
        # Check if GIF is found in search results for this tag
        tag_result = check_gif_in_search_for_tag(gif_id, tag, max_results=2500)
        results['tag_results'].append(tag_result)
        
        if tag_result['found']:
            results['tags_found'] += 1
            position = tag_result['position']
            total = tag_result['total_results']
            searched = tag_result['searched_count']
            
            # Log based on position (matches JavaScript logging style)
            if position <= 100:
                print(f"    🎯 EXCELLENT! GIF is in top 100 for tag '{tag}' (position {position})")
                print(f"       Total results: {total:,}, Searched: {searched:,}")
            elif position <= 500:
                print(f"    👍 GOOD! GIF is in top 500 for tag '{tag}' (position {position})")
                print(f"       Total results: {total:,}, Searched: {searched:,}")
            else:
                print(f"    📍 Found at position {position} for tag '{tag}'")
                print(f"       Total results: {total:,}, Searched: {searched:,}")
        else:
            results['tags_not_found'] += 1
            total = tag_result.get('total_results', 0)
            searched = tag_result.get('searched_count', 0)
            
            if searched > 0:
                print(f"    ❌ NOT FOUND in top {min(searched, 2500)} results")
                if total > 0:
                    print(f"       Tag has {total:,} total results - GIF might be beyond searchable range")
            else:
                print(f"    ❌ NOT FOUND - No search results returned for this tag")
            
            if tag_result.get('error'):
                print(f"       Error: {tag_result['error']}")
        
        # Small delay between tags to avoid rate limiting
        if i < len(tags):
            time.sleep(REQUEST_DELAY)
    
    # Calculate summary
    results['found_in_any_tag'] = results['tags_found'] > 0
    if results['total_tags_checked'] > 0:
        results['visibility_rate'] = (results['tags_found'] / results['total_tags_checked']) * 100
    
    # Print summary
    print(f"\n{'='*70}")
    print(f"📊 SUMMARY for GIF {gif_id}:")
    print(f"{'='*70}")
    print(f"   Tags checked: {results['total_tags_checked']}")
    print(f"   Tags found: {results['tags_found']}")
    print(f"   Tags not found: {results['tags_not_found']}")
    print(f"   Visibility rate: {results['visibility_rate']:.1f}%")
    print()
    
    if results['found_in_any_tag']:
        print(f"   ✅ GIF is VISIBLE in search results")
        # Show which tags found it
        found_tags = [r['tag'] for r in results['tag_results'] if r['found']]
        print(f"   Found in tags: {', '.join(found_tags)}")
        
        # Show positions
        print(f"\n   📍 Positions:")
        for r in results['tag_results']:
            if r['found']:
                print(f"      - '{r['tag']}': position {r['position']} (out of {r['total_results']:,} total)")
    else:
        print(f"   ❌ GIF is NOT VISIBLE in search results (likely SHADOW BANNED)")
        print(f"   All {results['total_tags_checked']} tags checked, but GIF not found in any search results")
    
    print(f"{'='*70}\n")
    
    return results


def test_gif_search_visibility(gif_id: str, tags: List[str] = None):
    """
    Test function to check if a specific GIF is visible in search results for its tags.
    
    Example usage:
        # Test the GIF from the image: TRxJO3PWikv59mtrVi
        tags = ['robot', 'everyday', 'robotics', 'bot', 'solution', 'needs', 'robotic', 
                'droids', 'home automation', 'open droids', 'ai robotics', 
                'future of robotics', 'tech for life', 'innovative gadgets', 
                'robot technology', 'smart droids', 'automated assistance', 'everyday solutions']
        test_gif_search_visibility('TRxJO3PWikv59mtrVi', tags)
    
    Or let it fetch tags automatically:
        test_gif_search_visibility('TRxJO3PWikv59mtrVi')
    
    Args:
        gif_id: The GIF ID to test
        tags: List of tags to check. If None, will fetch tags from the GIF
    """
    print(f"\n{'='*70}")
    print(f"🎯 GIF SEARCH VISIBILITY TESTER")
    print(f"{'='*70}")
    print(f"GIF ID: {gif_id}")
    
    # If tags not provided, fetch them from the GIF
    if tags is None:
        print("📥 Fetching tags from GIF...")
        tags = get_gif_tags(gif_id)
        if not tags:
            print("❌ Could not fetch tags from GIF. Please provide tags manually.")
            return None
        print(f"✅ Fetched {len(tags)} tags from GIF")
    
    print(f"Tags to check: {len(tags)}")
    print(f"Tags: {', '.join(tags[:10])}{'...' if len(tags) > 10 else ''}")
    print(f"{'='*70}\n")
    
    # Use the detailed tag checking function
    results = check_gif_tags_in_search(gif_id, tags)
    
    return results


# Example usage
if __name__ == '__main__':
    import sys
    
    # Example: Test the GIF from the image description
    # GIF ID: TRxJO3PWikv59mtrVi
    # Tags from the image description
    example_tags = [
        'robot',
        'everyday',
        'robotics',
        'bot',
        'solution',
        'needs',
        'robotic',
        'droids',
        'home automation',
        'open droids',
        'ai robotics',
        'future of robotics',
        'tech for life',
        'innovative gadgets',
        'robot technology',
        'smart droids',
        'automated assistance',
        'everyday solutions'
    ]
    
    # Get GIF ID from command line or use example
    if len(sys.argv) > 1:
        gif_id = sys.argv[1]
        # Get tags from command line if provided
        if len(sys.argv) > 2:
            tags = sys.argv[2].split(',')
            tags = [t.strip() for t in tags]
        else:
            tags = None  # Will fetch from GIF
    else:
        # Use example GIF ID
        gif_id = 'TRxJO3PWikv59mtrVi'
        tags = example_tags
    
    # Run the test
    test_gif_search_visibility(gif_id, tags)

