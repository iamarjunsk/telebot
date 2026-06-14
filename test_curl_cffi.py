#!/usr/bin/env python3
"""Test curl_cffi for Instagram post download"""

import re
import urllib.parse
from pathlib import Path
from curl_cffi import requests as curl_requests

BASE_DIR = Path('C:/Users/skang/telebot')
COOKIE_FILE = BASE_DIR / 'cookies.txt'

# Parse cookies
cookies = {}
for line in COOKIE_FILE.read_text().splitlines():
    if line.startswith('#') or not line.strip():
        continue
    parts = line.split('\t')
    if len(parts) >= 7 and 'instagram' in parts[0].lower():
        cookies[parts[5]] = urllib.parse.unquote(parts[6])

# Use curl_cffi to impersonate Chrome
session = curl_requests.Session(impersonate='chrome120')

# Set cookies
for name, value in cookies.items():
    session.cookies.set(name, value, domain='.instagram.com', path='/')

# Test fetching a post page
url = 'https://www.instagram.com/p/C-l4JzXP0iW/'
print('Testing curl_cffi browser impersonation...')
resp = session.get(url, timeout=30)
print(f'HTTP {resp.status_code}')
print(f'Content length: {len(resp.text)}')

# Check for media URLs
video_urls = re.findall(r'https?://[^\s"\'<>]+\.cdninstagram\.com/[^\s"\'<>]+\.mp4', resp.text)
image_urls = re.findall(r'https?://[^\s"\'<>]+\.cdninstagram\.com/[^\s"\'<>]+\.jpg', resp.text)
print(f'Found {len(video_urls)} video URLs, {len(image_urls)} image URLs')

if video_urls:
    print(f'✅ First video: {video_urls[0][:80]}...')
if image_urls:
    print(f'✅ First image: {image_urls[0][:80]}...')

# Also check for _sharedData
shared_data = re.search(r'window\._sharedData\s*=\s*({.+?});</script>', resp.text, re.DOTALL)
if shared_data:
    print('✅ Found _sharedData in page')
else:
    print('❌ No _sharedData found')

# Check for login state
if '"isLoggedIn":true' in resp.text or 'isLoggedIn' in resp.text:
    print('✅ Page indicates logged in state')
else:
    print('⚠️ No clear login indicator in page')
