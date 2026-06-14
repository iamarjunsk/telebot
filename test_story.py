#!/usr/bin/env python3
"""Test Instagram story download"""

import asyncio
import sys
from pathlib import Path

# Add the bot directory to path
sys.path.insert(0, str(Path(__file__).parent))

from bot import ig_downloader

async def test():
    url = "https://www.instagram.com/stories/therealprithvi/3913713162512131212?utm_source=ig_story_item_share&igsh=d3hrMXpzZjdndjdp"
    download_id = "test_123"
    
    print("="*60)
    print(f"Testing story download:")
    print(f"URL: {url}")
    print("="*60)
    
    # Test URL parsing
    story_info = ig_downloader.is_story_url(url)
    if story_info:
        print(f"✅ URL parsed: username={story_info[0]}, story_id={story_info[1]}")
    else:
        print("❌ Failed to parse story URL")
        return
    
    # Test download
    print("\nStarting download...")
    result = await ig_downloader.download(url, download_id)
    
    print("\n" + "="*60)
    print("RESULT:")
    print(f"Success: {result.get('success')}")
    print(f"Method: {result.get('method', 'N/A')}")
    print(f"Error: {result.get('error', 'None')}")
    if result.get('files'):
        print(f"Files: {result['files']}")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(test())
