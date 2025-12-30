"""
Daily script to update view counts for all tracked channels.
Run this script daily (e.g., via cron job or scheduled task) to track view trends.

Usage:
    python update_views_daily.py

Or schedule with cron (Linux/Mac):
    0 2 * * * /usr/bin/python3 /path/to/update_views_daily.py

Or with Windows Task Scheduler:
    Schedule a task to run daily at a specific time
"""

import sqlite3
import sys
import os

# Add the current directory to the path to import app functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import update_gif_views_batch, get_channel_gifs, DB_NAME

def update_all_channels():
    """Update view counts for all channels in the database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all channels
    cursor.execute('SELECT DISTINCT channel_id FROM channels')
    channels = cursor.fetchall()
    conn.close()
    
    total_channels = len(channels)
    print(f"Found {total_channels} channels to update")
    
    for idx, (channel_id,) in enumerate(channels, 1):
        print(f"\n[{idx}/{total_channels}] Updating channel: {channel_id}")
        
        try:
            # Get all GIFs for this channel
            gifs = get_channel_gifs(channel_id)
            
            if not gifs:
                print(f"  No GIFs found for channel {channel_id}")
                continue
            
            gif_ids = [gif['gif_id'] for gif in gifs]
            print(f"  Updating {len(gif_ids)} GIFs...")
            
            # Update views
            results = update_gif_views_batch(gif_ids, max_workers=5)
            
            successful = sum(1 for r in results if r.get('success'))
            failed = len(results) - successful
            
            print(f"  ✓ Updated: {successful} successful, {failed} failed")
            
        except Exception as e:
            print(f"  ✗ Error updating channel {channel_id}: {str(e)}")
            continue
    
    print(f"\n✓ Completed updating all channels")

if __name__ == '__main__':
    update_all_channels()

