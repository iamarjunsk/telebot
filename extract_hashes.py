#!/usr/bin/env python3
"""Extract current Instagram query hashes from the main page"""

import re
import urllib.parse
import requests
from pathlib import Path

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

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'X-IG-App-ID': '936619743392459',
    'X-ASBD-ID': '129477',
    'Referer': 'https://www.instagram.com/',
}

session = requests.Session()
session.headers.update(headers)
for name, value in cookies.items():
    session.cookies.set(name, value, domain='.instagram.com', path='/')

# Fetch the main page
resp = session.get('https://www.instagram.com/', timeout=30)
html = resp.text

# Look for query hashes in the HTML
query_hashes = re.findall(r'queryId["\']:(["\'])([a-f0-9]{32})\1', html)
print(f'Found {len(query_hashes)} query hashes in main page')
for match in query_hashes[:10]:
    print(f'  {match[1]}')

# Look for JS files that might contain query hashes
js_pattern = r'https?://[^\s"\'<>]+/consumer_lib_common[^\s"\'<>]*\.js'
js_urls = re.findall(js_pattern, html)
print(f'\nFound {len(js_urls)} consumer_lib_common JS files')
for js_url in js_urls[:3]:
    print(f'  {js_url}')

# Also check for any 32-char hex strings that look like query hashes
all_hashes = re.findall(r'[a-f0-9]{32}', html)
print(f'\nTotal 32-char hex strings: {len(all_hashes)}')
unique_hashes = list(set(all_hashes))
print(f'Unique: {len(unique_hashes)}')
for h in unique_hashes[:10]:
    print(f'  {h}')
