#!/usr/bin/env python3
"""Diagnose Instagram download methods and session state"""

import os
import sys
import json
import time
import asyncio
import requests
from pathlib import Path

# Add bot directory to path
sys.path.insert(0, str(Path(__file__).parent))

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp_diagnose"
TEMP_DIR.mkdir(exist_ok=True)

def check_session():
    """Check current session state"""
    print("=" * 60)
    print("SESSION DIAGNOSTICS")
    print("=" * 60)
    
    session_file = BASE_DIR / "session_halo_aju2_"
    if session_file.exists():
        age_hours = (time.time() - session_file.stat().st_mtime) / 3600
        print(f"❌ Session file age: {age_hours:.1f} hours (max 12h recommended)")
        print(f"   File: {session_file}")
    else:
        print("❌ No session file found")
    
    cookie_file = BASE_DIR / "cookies.txt"
    if cookie_file.exists():
        lines = cookie_file.read_text().splitlines()
        ig_cookies = [l for l in lines if 'instagram' in l.lower() and not l.startswith('#')]
        print(f"\ncookies.txt: {len(ig_cookies)} Instagram cookies")
        
        cookie_names = set()
        for l in ig_cookies:
            parts = l.split('\t')
            if len(parts) >= 7:
                cookie_names.add(parts[5])
        
        critical = ['sessionid', 'ds_user_id', 'csrftoken']
        for c in critical:
            if c in cookie_names:
                print(f"  ✅ {c}")
            else:
                print(f"  ❌ {c} MISSING — required for authenticated content")
        
        print(f"  Found cookies: {', '.join(cookie_names)}")
    else:
        print("❌ No cookies.txt found")
    
    # Check browser cookies
    print("\n--- Browser Cookies ---")
    try:
        import browser_cookie3
        for browser_name, browser_fn in [
            ("Chrome", browser_cookie3.chrome),
            ("Edge", browser_cookie3.edge),
            ("Firefox", browser_cookie3.firefox),
        ]:
            try:
                jar = browser_fn(domain_name="instagram.com")
                ig_cookies = [c for c in jar if "instagram" in c.domain]
                names = {c.name for c in ig_cookies}
                has_session = 'sessionid' in names
                print(f"  {browser_name}: {len(ig_cookies)} cookies, sessionid={'✅' if has_session else '❌'}")
            except Exception as e:
                print(f"  {browser_name}: Failed — {e}")
    except ImportError:
        print("  browser_cookie3 not installed")
    
    print()

def test_instaloader_post():
    """Test instaloader with a public post"""
    print("=" * 60)
    print("TEST 1: Instaloader Public Post")
    print("=" * 60)
    
    try:
        import instaloader
        L = instaloader.Instaloader()
        
        # Try loading session
        session_file = BASE_DIR / "session_halo_aju2_"
        if session_file.exists():
            try:
                L.load_session_from_file("halo_aju2", str(session_file))
                print("✅ Loaded old session file")
            except Exception as e:
                print(f"⚠️ Session load failed: {e}")
        
        # Test with a well-known public post
        test_shortcode = "C0zQqB3yZ-"  # Meta's own post
        print(f"Testing shortcode: {test_shortcode}")
        
        try:
            post = instaloader.Post.from_shortcode(L.context, test_shortcode)
            print(f"✅ Post fetched: {post.shortcode}")
            print(f"   Owner: {post.owner_username}")
            print(f"   Is video: {post.is_video}")
            print(f"   URL: {post.url}")
        except Exception as e:
            print(f"❌ Failed: {e}")
    except ImportError:
        print("❌ instaloader not installed")
    
    print()

def test_ytdlp():
    """Test yt-dlp with Instagram"""
    print("=" * 60)
    print("TEST 2: yt-dlp Instagram")
    print("=" * 60)
    
    try:
        from yt_dlp import YoutubeDL
        
        # Test with a public reel
        url = "https://www.instagram.com/reel/C0zQqB3yZ-/"
        
        opts = {
            'format': 'best',
            'cookiefile': str(BASE_DIR / 'cookies.txt'),
            'quiet': True,
            'no_warnings': True,
        }
        
        with YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                print(f"✅ yt-dlp extracted info")
                print(f"   Title: {info.get('title', 'N/A')}")
                print(f"   Formats: {len(info.get('formats', []))}")
            except Exception as e:
                print(f"❌ yt-dlp failed: {e}")
    except ImportError:
        print("❌ yt-dlp not installed")
    
    print()

def test_graphql_api():
    """Test Instagram GraphQL with current cookies"""
    print("=" * 60)
    print("TEST 3: Instagram GraphQL API")
    print("=" * 60)
    
    cookie_file = BASE_DIR / "cookies.txt"
    if not cookie_file.exists():
        print("❌ No cookies.txt to test with")
        print()
        return
    
    # Parse cookies
    cookies = {}
    for line in cookie_file.read_text().splitlines():
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 7 and 'instagram' in parts[0].lower():
            cookies[parts[5]] = parts[6]
    
    if 'sessionid' not in cookies:
        print("❌ No sessionid in cookies — cannot test GraphQL")
        print()
        return
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "129477",
        "Referer": "https://www.instagram.com/",
    }
    
    session = requests.Session()
    session.headers.update(headers)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".instagram.com", path="/")
    
    # Test user lookup
    test_user = "instagram"
    try:
        resp = session.get(
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": test_user},
            timeout=30
        )
        print(f"User lookup status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            user = data.get("data", {}).get("user", {})
            print(f"✅ GraphQL user lookup works")
            print(f"   User ID: {user.get('id')}")
            print(f"   Username: {user.get('username')}")
        else:
            print(f"❌ GraphQL failed: {resp.text[:200]}")
    except Exception as e:
        print(f"❌ GraphQL error: {e}")
    
    print()

def test_gallery_dl():
    """Test gallery-dl availability"""
    print("=" * 60)
    print("TEST 4: gallery-dl")
    print("=" * 60)
    
    import shutil
    import subprocess
    
    if not shutil.which("gallery-dl"):
        print("❌ gallery-dl not found in PATH")
        print("   Install: pip install gallery-dl")
    else:
        print("✅ gallery-dl found")
        try:
            result = subprocess.run(["gallery-dl", "--version"], capture_output=True, text=True)
            print(f"   Version: {result.stdout.strip()}")
        except Exception as e:
            print(f"   Version check failed: {e}")
    
    print()

def test_curl_cffi():
    """Test curl_cffi availability"""
    print("=" * 60)
    print("TEST 5: curl_cffi")
    print("=" * 60)
    
    try:
        from curl_cffi import requests as curl_requests
        print("✅ curl_cffi installed")
        
        # Quick test fetch
        session = curl_requests.Session(impersonate="chrome120")
        resp = session.get("https://www.instagram.com/", timeout=10)
        print(f"   Instagram fetch: {resp.status_code}")
    except ImportError:
        print("❌ curl_cffi not installed")
        print("   Install: pip install curl-cffi")
    except Exception as e:
        print(f"⚠️ curl_cffi import ok but fetch failed: {e}")
    
    print()

def main():
    print("\n" + "=" * 60)
    print("INSTAGRAM DOWNLOADER DIAGNOSTICS")
    print("=" * 60 + "\n")
    
    check_session()
    test_instaloader_post()
    test_ytdlp()
    test_graphql_api()
    test_gallery_dl()
    test_curl_cffi()
    
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("""
The main issues are likely:
1. Session is 1200+ hours old — Instagram requires fresh auth
2. cookies.txt lacks 'sessionid' and 'ds_user_id' 
3. Without these, age-restricted content and stories are blocked

SOLUTION:
- Log into Instagram in Chrome/Edge on this machine
- Run the bot's interactive login or extract_cookies.py
- Or use browser_cookie3 to pull fresh cookies automatically
""")

if __name__ == "__main__":
    main()
