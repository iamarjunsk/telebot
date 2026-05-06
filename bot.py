#!/usr/bin/env python3
"""
Instagram & YouTube Downloader Bot - Linux/Pop!_OS Optimized
"""

import os
import re
import sys
import time
import asyncio
import logging
import shutil
import html
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded .env file")
except ImportError:
    pass

import instaloader
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SERVER_IP = os.getenv("SERVER_IP", "100.97.53.84")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8080"))
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

if not BOT_TOKEN:
    print("❌ ERROR: Set BOT_TOKEN in .env file")
    sys.exit(1)

# Local project path for stability
BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp_downloads"
TEMP_DIR.mkdir(exist_ok=True)
COOKIE_FILE = BASE_DIR / "cookies.txt"
SERVER_IP = os.getenv("SERVER_IP", "100.97.53.84")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8080"))
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

def start_file_server():
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import os
    os.chdir(TEMP_DIR)
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
    def run():
        HTTPServer(('', SERVER_PORT), Handler).serve_forever()
    threading.Thread(target=run, daemon=True).start()
    print(f"📁 File server running on http://{SERVER_IP}:{SERVER_PORT}")

start_file_server()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# INSTAGRAM DOWNLOADER
# ============================================================================

class InstagramDownloader:
    def __init__(self):
        self.L = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            save_metadata=False,
            request_timeout=60
        )
        self.yt_dlp_available = self._check_ytdlp()
        self._login()
    
    def _check_ytdlp(self):
        try:
            import yt_dlp
            return True
        except ImportError:
            return False
    
    def _login(self):
        session_file = BASE_DIR / f"session_{IG_USERNAME}"
        
        # 1. Try browser cookies first ( freshest, auto-updated by system browser )
        if self._load_browser_cookies():
            print("✅ Logged in via system browser cookies")
            self.L.save_session_to_file(str(session_file))
            self._export_cookies_to_ytdlp()
            return
        
        # 2. Try to load saved session file
        if session_file.exists():
            try:
                self.L.load_session_from_file(IG_USERNAME, str(session_file))
                if self._validate_session():
                    print(f"✅ Loaded Instagram session from file: {session_file.name}")
                    self._export_cookies_to_ytdlp()
                    return
                else:
                    print("⚠️ Session file loaded but validation failed (expired)")
            except Exception as e:
                print(f"⚠️ Session file failed: {e}")

        # 3. Try headless browser login (macOS-friendly, bypasses sandbox)
        if IG_USERNAME and IG_PASSWORD:
            if self._playwright_login():
                self.L.save_session_to_file(str(session_file))
                self._export_cookies_to_ytdlp()
                return

        # 4. Try regular login
        if IG_USERNAME and IG_PASSWORD:
            try:
                print(f"🔑 Attempting direct API login for {IG_USERNAME}...")
                self.L.login(IG_USERNAME, IG_PASSWORD)
                self.L.save_session_to_file(str(session_file))
                print("✅ Login successful and session saved")
                self._export_cookies_to_ytdlp()
            except Exception as e:
                print(f"❌ Password login failed: {e}")
                print("💡 Tip: Log into Instagram in Chrome/Safari on this machine.")
    
    def _playwright_login(self) -> bool:
        """Use headless browser to log into Instagram and extract cookies."""
        try:
            from playwright.sync_api import sync_playwright
            import requests
            
            print("🎭 Starting headless browser login...")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                
                # Go to login page
                page.goto("https://www.instagram.com/accounts/login/", wait_until="networkidle")
                page.wait_for_timeout(2500)
                
                # Accept cookies if prompted
                try:
                    accept_selectors = [
                        "button:has-text('Allow all cookies')",
                        "button:has-text('Accept')", 
                        "button:has-text('Allow essential and optional cookies')",
                        "[role='button']:has-text('Allow all cookies')",
                    ]
                    for sel in accept_selectors:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            page.wait_for_timeout(1000)
                            break
                except:
                    pass
                
                # Check if already logged in (redirected to home)
                current_url = page.url
                if "instagram.com" in current_url and "login" not in current_url:
                    print("   Already logged in (redirected from login page)")
                else:
                    # Fill login form with multiple selector fallbacks
                    username_filled = False
                    for username_sel in ["input[name='username']", "input[aria-label='Phone number, username, or email']", "input[type='text']"]:
                        try:
                            page.locator(username_sel).first.fill(IG_USERNAME)
                            username_filled = True
                            break
                        except:
                            continue
                    
                    password_filled = False
                    for pwd_sel in ["input[name='password']", "input[aria-label='Password']", "input[type='password']"]:
                        try:
                            page.locator(pwd_sel).first.fill(IG_PASSWORD)
                            password_filled = True
                            break
                        except:
                            continue
                    
                    if not username_filled or not password_filled:
                        print("   Could not find login form fields. Instagram UI may have changed.")
                        browser.close()
                        return False
                    
                    # Click login button
                    for btn_sel in ["button[type='submit']", "[role='button']:has-text('Log in')", "button:has-text('Log in')"]:
                        try:
                            page.locator(btn_sel).first.click()
                            break
                        except:
                            continue
                    
                    # Wait for navigation after login
                    page.wait_for_timeout(4000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except:
                        pass
                
                page.wait_for_timeout(2000)
                
                # Check for 2FA / challenge / suspicious login
                current_url = page.url
                if any(x in current_url for x in ["challenge", "two_factor", "confirm_email", "suspicious"]):
                    print("   ⚠️ Instagram requires 2FA/challenge. Complete it in a real browser first.")
                    browser.close()
                    return False
                
                # Check if login succeeded by looking for logged-in indicators
                logged_in = False
                for indicator in ["a[href='/direct/inbox/']", "[aria-label='Direct']", "nav", "[role='navigation']", "svg[aria-label='Home']"]:
                    try:
                        if page.locator(indicator).first.is_visible(timeout=3000):
                            logged_in = True
                            break
                    except:
                        continue
                
                if not logged_in and "login" in page.url:
                    # Check for error message
                    error_selectors = [
                        "[data-testid='login-error-message']",
                        "#slfErrorAlert",
                        "p:has-text('password was incorrect')",
                        "p:has-text('username')",
                    ]
                    for sel in error_selectors:
                        try:
                            if page.locator(sel).first.is_visible(timeout=2000):
                                print("   ❌ Instagram rejected the login credentials.")
                                browser.close()
                                return False
                        except:
                            continue
                    
                    print("   ⚠️ Still on login page. Login may have failed.")
                    browser.close()
                    return False
                
                # Extract cookies
                cookies = context.cookies()
                ig_cookies = [c for c in cookies if "instagram" in c.get("domain", "")]
                print(f"   Extracted {len(ig_cookies)} Instagram cookies from browser")
                
                if len(ig_cookies) < 3:
                    print("   ⚠️ Too few cookies extracted. Login may have failed.")
                    browser.close()
                    return False
                
                # Build requests session and inject into instaloader
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                
                for c in ig_cookies:
                    session.cookies.set(
                        name=c["name"],
                        value=c["value"],
                        domain=c["domain"],
                        path=c.get("path", "/"),
                        secure=c.get("secure", True),
                        expires=c.get("expires"),
                    )
                
                self.L.context._session = session
                browser.close()
            
            # Validate the session
            if self._validate_session():
                print("✅ Headless browser login successful")
                return True
            else:
                print("⚠️ Browser login appeared to succeed but session is invalid")
                return False
                
        except ImportError:
            print("   Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
            return False
        except Exception as e:
            print(f"   Headless browser login failed: {e}")
            return False
    
    def _load_browser_cookies(self) -> bool:
        """Extract Instagram cookies from system browser and inject into instaloader session."""
        try:
            import browser_cookie3
            import requests
            import sys
            
            print("🌐 Trying to load Instagram cookies from system browser...")
            
            # Reorder browsers by OS (most common first)
            if sys.platform == "darwin":
                browsers = [
                    browser_cookie3.safari,
                    browser_cookie3.chrome,
                    browser_cookie3.firefox,
                    browser_cookie3.edge,
                ]
            elif sys.platform == "win32":
                browsers = [
                    browser_cookie3.chrome,
                    browser_cookie3.edge,
                    browser_cookie3.firefox,
                ]
            else:  # Linux
                browsers = [
                    browser_cookie3.chrome,
                    browser_cookie3.firefox,
                    browser_cookie3.edge,
                ]
            
            jar = None
            found_browser = None
            for browser_fn in browsers:
                try:
                    jar = browser_fn(domain_name="instagram.com")
                    if jar:
                        cookies_list = list(jar)
                        ig_cookies = [c for c in cookies_list if "instagram" in c.domain]
                        if len(ig_cookies) >= 3:
                            found_browser = browser_fn.__name__
                            print(f"   Found {len(ig_cookies)} Instagram cookies from {found_browser}")
                            break
                except Exception:
                    continue
            
            if not jar or not found_browser:
                print("   No browser cookies found. Log into Instagram in Chrome/Edge.")
                return False
            
            session = requests.Session()
            for cookie in jar:
                if "instagram" in cookie.domain:
                    session.cookies.set(
                        name=cookie.name,
                        value=cookie.value,
                        domain=cookie.domain,
                        path=cookie.path or "/",
                        secure=cookie.secure,
                        expires=cookie.expires,
                    )
            
            self.L.context._session = session
            
            if self._validate_session():
                return True
            else:
                print("   Browser cookies loaded but session invalid (maybe wrong account?)")
                return False
                
        except ImportError:
            print("   browser_cookie3 not installed. Run: pip install browser_cookie3")
            return False
        except Exception as e:
            print(f"   Browser cookie load failed: {e}")
            return False
    
    def _validate_session(self) -> bool:
        """Check if the current session is valid for stories (strict check)."""
        try:
            from instaloader import Profile
            # Strict: try fetching a profile AND its stories feed
            # Basic profile fetch works with stale sessions, stories don't
            profile = Profile.from_username(self.L.context, IG_USERNAME)
            _ = profile.userid
            # Also verify sessionid cookie exists (required for stories)
            session_cookies = self.L.context._session.cookies if hasattr(self.L.context, '_session') else None
            if session_cookies:
                has_sessionid = any(c.name == 'sessionid' for c in session_cookies)
                if not has_sessionid:
                    print("   No sessionid cookie found")
                    return False
            return True
        except Exception as e:
            print(f"🔍 Session validation error: {e}")
            return False
    
    def _force_fresh_login(self):
        """Delete old session and force a brand new login."""
        session_file = BASE_DIR / f"session_{IG_USERNAME}"
        print("🔄 Forcing fresh login...")
        
        # Delete old session
        if session_file.exists():
            try:
                session_file.unlink()
                print(f"   Deleted old session file: {session_file.name}")
            except Exception as e:
                print(f"   Could not delete session file: {e}")
        
        # Also delete cookies.txt
        if COOKIE_FILE.exists():
            try:
                COOKIE_FILE.unlink()
                print(f"   Deleted old cookies.txt")
            except Exception:
                pass
        
        # Try Playwright first, then direct login
        if self._playwright_login():
            self.L.save_session_to_file(str(session_file))
            self._export_cookies_to_ytdlp()
            return True
        
        if IG_USERNAME and IG_PASSWORD:
            try:
                self.L.login(IG_USERNAME, IG_PASSWORD)
                self.L.save_session_to_file(str(session_file))
                print("✅ Fresh direct login successful")
                self._export_cookies_to_ytdlp()
                return True
            except Exception as e:
                print(f"❌ Fresh direct login failed: {e}")
        
        return False
    
    def _export_cookies_to_ytdlp(self):
        """Export instaloader's live session cookies to a Netscape cookies.txt for yt-dlp."""
        try:
            import http.cookiejar as cookiejar
            
            session = self.L.context._session
            if not session or not session.cookies:
                return
                
            cj = cookiejar.MozillaCookieJar(str(COOKIE_FILE))
            for cookie in session.cookies:
                # Build rest dict for HttpOnly flag
                rest = {}
                if hasattr(cookie, '_rest') and cookie._rest and cookie._rest.get("HttpOnly"):
                    rest = {"HttpOnly": ""}
                
                # Convert requests cookie to cookielib cookie
                c = cookiejar.Cookie(
                    version=0,
                    name=cookie.name,
                    value=cookie.value,
                    port=None,
                    port_specified=False,
                    domain=cookie.domain if cookie.domain else ".instagram.com",
                    domain_specified=bool(cookie.domain),
                    domain_initial_dot=cookie.domain.startswith(".") if cookie.domain else True,
                    path=cookie.path if cookie.path else "/",
                    path_specified=bool(cookie.path),
                    secure=cookie.secure,
                    expires=cookie.expires if cookie.expires else None,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest=rest,
                    rfc2109=False,
                )
                cj.set_cookie(c)
            cj.save(ignore_discard=True, ignore_expires=True)
            print(f"🍪 Exported live session cookies to {COOKIE_FILE.name}")
        except Exception as e:
            print(f"⚠️ Cookie export failed: {e}")

    async def download(self, url: str, download_id: str) -> dict:
        if self.is_story_url(url):
            temp_dir = TEMP_DIR / f"ig_{download_id}"
            temp_dir.mkdir(exist_ok=True)
            # Try instaloader first (has valid session)
            result = await self.download_story(url, download_id)
            if result.get("success"):
                return result
            # If instaloader failed with login error and we couldn't fix it, don't bother with yt-dlp
            error_str = str(result.get("error", "")).lower()
            if "login" in error_str or "session expired" in error_str:
                return result
            if self.yt_dlp_available:
                print("🔄 Story download via instaloader failed, trying yt-dlp...")
                return await self._download_with_ytdlp(url, temp_dir)
            return result
        
        temp_dir = TEMP_DIR / f"ig_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        shortcode = self.extract_shortcode(url)
        
        if not shortcode:
            return {"success": False, "error": "Invalid Instagram URL"}

        await asyncio.sleep(1)

        for attempt in range(2):
            if attempt > 0:
                wait_time = attempt * 2
                print(f"⏳ Retry {attempt + 1} after {wait_time}s delay...")
                await asyncio.sleep(wait_time)
                try:
                    print(f"🔄 Re-logging to refresh session...")
                    self._login()
                except Exception:
                    pass
                await asyncio.sleep(2)
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir.mkdir(exist_ok=True)

            try:
                print(f"📥 Instaloader attempt {attempt + 1}: {shortcode}")
                post = instaloader.Post.from_shortcode(self.L.context, shortcode)
                self.L.dirname_pattern = str(temp_dir)
                self.L.download_post(post, target=shortcode)
                
                files = self._collect_files(temp_dir)
                if files:
                    return {
                        "success": True, "files": files, "method": "instaloader",
                        "caption": post.caption[:400] if post.caption else "",
                        "author": post.owner_username, "temp_dir": str(temp_dir)
                    }
            except Exception as e:
                error_str = str(e).lower()
                print(f"⚠️ Instaloader attempt {attempt + 1} failed: {e}")
                
                if 'rate' in error_str or 'blocked' in error_str or 'login' in error_str:
                    if attempt < 1:
                        await asyncio.sleep(3)
                        continue
                
                if self.yt_dlp_available:
                    print(f"🔄 Falling back to yt-dlp...")
                    yt_result = await self._download_with_ytdlp(url, temp_dir)
                    if yt_result.get("success"):
                        return yt_result
                    elif attempt < 1:
                        await asyncio.sleep(3)
                        continue

        if self.yt_dlp_available:
            return await self._download_with_ytdlp(url, temp_dir)
        
        return {"success": False, "error": "Download failed after multiple attempts. Content may be private, restricted, or rate-limited."}

    async def _download_with_ytdlp(self, url: str, temp_dir: Path, retry_count: int = 0) -> dict:
        max_retries = 2
        try:
            from yt_dlp import YoutubeDL
            output_path = str(temp_dir / "inst_%(title)s.%(ext)s")
            
            ydl_opts = {
                'format': 'best[filesize<50M]/best',
                'outtmpl': output_path,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'extractor_retries': 5,
                'fragment_retries': 5,
                'retries': 5,
                'socket_timeout': 30,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                },
            }
            
            # Prefer live exported cookies, then browser, then username/password
            if COOKIE_FILE.exists():
                ydl_opts['cookiefile'] = str(COOKIE_FILE)
            else:
                # Try to read directly from system browser as fallback
                if sys.platform == "darwin":
                    ydl_opts['cookiesfrombrowser'] = ('safari',)
                elif sys.platform == "win32":
                    ydl_opts['cookiesfrombrowser'] = ('chrome',)
                elif IG_USERNAME and IG_PASSWORD:
                    ydl_opts['username'] = IG_USERNAME
                    ydl_opts['password'] = IG_PASSWORD

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
            files = self._collect_files(temp_dir)
            if files:
                return {
                    "success": True, "files": files, "method": "yt-dlp",
                    "caption": info.get('title', '')[:400],
                    "author": info.get('uploader', 'unknown'), "temp_dir": str(temp_dir)
                }
        except Exception as e:
            error_msg = str(e)
            
            if retry_count < max_retries and any(x in error_msg.lower() for x in ['rate-limit', 'rate limit', '429', 'too many requests']):
                wait_time = (retry_count + 1) * 3
                print(f"⚠️ Rate limited, waiting {wait_time}s before retry ({retry_count + 1}/{max_retries})...")
                await asyncio.sleep(wait_time)
                return await self._download_with_ytdlp(url, temp_dir, retry_count + 1)
            
            return {"success": False, "error": f"yt-dlp failed: {error_msg[:100]}"}
        return {"success": False, "error": "No files found."}

    def _collect_files(self, temp_dir: Path) -> list:
        return [str(f) for f in temp_dir.iterdir() if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']]

    def extract_shortcode(self, url: str) -> str:
        m = re.search(r'instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
        return m.group(1) if m else None

    def is_story_url(self, url: str):
        m = re.search(r'instagram\.com/stories/([^/]+)/(\d+)', url)
        if m:
            return (m.group(1), m.group(2))
        return None

    async def download_story(self, url: str, download_id: str, allow_relogin: bool = True) -> dict:
        temp_dir = TEMP_DIR / f"ig_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        
        story_info = self.is_story_url(url)
        if not story_info:
            return {"success": False, "error": "Invalid story URL"}
        
        username, story_id = story_info
        
        try:
            print(f"📥 Downloading story: {username} ({story_id})")
            from instaloader import Profile, StoryItem
            
            self.L.dirname_pattern = str(temp_dir)
            
            # Try to download the specific story by media ID first
            try:
                story = StoryItem.from_mediaid(self.L.context, int(story_id))
                self.L.download_storyitem(story, target=username)
                print(f"✅ Downloaded specific story item {story_id}")
            except Exception as inner_e:
                inner_err = str(inner_e).lower()
                print(f"⚠️ Specific story fetch failed: {inner_e}")
                
                # Check if it's a login error
                if any(x in inner_err for x in ['login', 'unauthorized', '401', '403', 'redirect']) and allow_relogin:
                    print("   Detected login error. Forcing fresh login and retrying...")
                    if self._force_fresh_login():
                        return await self.download_story(url, download_id, allow_relogin=False)
                    else:
                        return {"success": False, "error": "Session expired. Fresh login failed."}
                
                print(f"   Trying all stories from user...")
                profile = Profile.from_username(self.L.context, username)
                self.L.download_stories(userids=[profile.userid], target=username)
            
            files = self._collect_files(temp_dir)
            if files:
                return {
                    "success": True, "files": files, "method": "instaloader",
                    "caption": "", "author": username, "temp_dir": str(temp_dir)
                }
        except Exception as e:
            err_str = str(e).lower()
            print(f"⚠️ Story download failed: {e}")
            
            # Check if it's a login error and retry once
            if any(x in err_str for x in ['login', 'unauthorized', '401', '403', 'redirect']) and allow_relogin:
                print("   Detected login error. Forcing fresh login and retrying...")
                if self._force_fresh_login():
                    return await self.download_story(url, download_id, allow_relogin=False)
                else:
                    return {"success": False, "error": "Session expired. Fresh login failed."}
        
        return {"success": False, "error": "Failed to download story"}

ig_downloader = InstagramDownloader()

# ============================================================================
# YOUTUBE DOWNLOADER
# ============================================================================

class YouTubeDownloader:
    async def download(self, url: str, download_id: str) -> dict:
        temp_dir = TEMP_DIR / f"yt_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
                'outtmpl': str(temp_dir / "yt_%(id)s.%(ext)s"),
                'prefer_free_formats': True,
                'retries': 5,
                'socket_timeout': 30,
                'fragment_retries': 5,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            
            files = list(temp_dir.iterdir())
            if files:
                video_file = max(files, key=lambda x: x.stat().st_size)
                return {"success": True, "file": str(video_file), "title": info.get('title'), "temp_dir": str(temp_dir)}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Download failed"}

yt_downloader = YouTubeDownloader()

# ============================================================================
# TWITTER/X DOWNLOADER
# ============================================================================

class TwitterDownloader:
    async def download(self, url: str, download_id: str) -> dict:
        temp_dir = TEMP_DIR / f"tw_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                'format': 'best[filesize<50M]/best',
                'outtmpl': str(temp_dir / "tw_%(id)s.%(ext)s"),
                'retries': 5,
                'socket_timeout': 30,
                'fragment_retries': 5,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/605.1.15',
                },
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            
            files = list(temp_dir.iterdir())
            if files:
                video_file = max(files, key=lambda x: x.stat().st_size)
                return {
                    "success": True,
                    "file": str(video_file),
                    "title": info.get('description', '')[:400],
                    "author": info.get('uploader', 'unknown'),
                    "temp_dir": str(temp_dir)
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Download failed"}

tw_downloader = TwitterDownloader()

# ============================================================================
# BOT HANDLERS
# ============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "instagram.com" in url or "instagr.am" in url:
        platform = "instagram"
    elif "youtube.com" in url or "youtu.be" in url:
        platform = "youtube"
    elif "x.com" in url or "twitter.com" in url:
        platform = "twitter"
    else: return

    msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")
    download_id = f"{update.effective_user.id}_{int(time.time())}"

    try:
        if platform == "instagram":
            res = await ig_downloader.download(url, download_id)
            if res["success"]:
                small_files = []
                large_files = []
                
                for f in res["files"]:
                    file_size = Path(f).stat().st_size
                    if file_size > MAX_UPLOAD_SIZE:
                        filename = f"{download_id}_{Path(f).name}"
                        dest = TEMP_DIR / filename
                        shutil.move(f, dest)
                        large_files.append((Path(f).name, dest))
                    else:
                        small_files.append(f)
                
                if large_files:
                    links = "\n".join([f"🔗 {name}: http://{SERVER_IP}:{SERVER_PORT}/{dest.name}" for name, dest in large_files])
                    await msg.edit_text(f"📎 Large files:\n{links}\n\n⏰ Links expire in 1 hour")
                
                if small_files:
                    await msg.edit_text(f"📤 Sending album...")
                    batch_size = 10
                    for batch_idx in range(0, len(small_files), batch_size):
                        batch = small_files[batch_idx:batch_idx + batch_size]
                        media_group = []
                        for i, f in enumerate(batch):
                            with open(f, 'rb') as file:
                                if f.lower().endswith(('.mp4', '.mov')):
                                    media_group.append(InputMediaVideo(file, caption=f"👤 @{res['author']}" if batch_idx + i == 0 else None))
                                else:
                                    media_group.append(InputMediaPhoto(file))
                        await update.message.reply_media_group(media_group, read_timeout=300)
                
                shutil.rmtree(res["temp_dir"], ignore_errors=True)
                await msg.delete()
            else:
                await msg.edit_text(f"❌ {res['error']}")
        elif platform == "youtube":
            res = await yt_downloader.download(url, download_id)
            if res["success"]:
                file_size = Path(res["file"]).stat().st_size
                if file_size > MAX_UPLOAD_SIZE:
                    filename = f"{download_id}_{Path(res['file']).name}"
                    dest = TEMP_DIR / filename
                    shutil.move(res["file"], dest)
                    link = f"http://{SERVER_IP}:{SERVER_PORT}/{filename}"
                    await msg.edit_text(f"📎 File too large for Telegram: {res['title']}\n\n🔗 Download: {link}\n\n⏰ Link expires in 1 hour")
                else:
                    with open(res["file"], 'rb') as f:
                        await update.message.reply_video(video=f, caption=res['title'], read_timeout=300, write_timeout=300)
                    shutil.rmtree(res["temp_dir"], ignore_errors=True)
                    await msg.delete()
            else:
                await msg.edit_text(f"❌ {res['error']}")
        elif platform == "twitter":
            res = await tw_downloader.download(url, download_id)
            if res["success"]:
                file_size = Path(res["file"]).stat().st_size
                if file_size > MAX_UPLOAD_SIZE:
                    filename = f"{download_id}_{Path(res['file']).name}"
                    dest = TEMP_DIR / filename
                    shutil.move(res["file"], dest)
                    link = f"http://{SERVER_IP}:{SERVER_PORT}/{filename}"
                    await msg.edit_text(f"📎 File too large for Telegram: {res['title'][:200]}\n\n🔗 Download: {link}\n\n⏰ Link expires in 1 hour")
                else:
                    with open(res["file"], 'rb') as f:
                        await update.message.reply_video(video=f, caption=res.get('title', '')[:1000], read_timeout=300, write_timeout=300)
                    shutil.rmtree(res["temp_dir"], ignore_errors=True)
                    await msg.delete()
            else:
                await msg.edit_text(f"❌ {res['error']}")
        else:
            res = await yt_downloader.download(url, download_id)
            if res["success"]:
                file_size = Path(res["file"]).stat().st_size
                if file_size > MAX_UPLOAD_SIZE:
                    filename = f"{download_id}_{Path(res['file']).name}"
                    dest = TEMP_DIR / filename
                    shutil.move(res["file"], dest)
                    link = f"http://{SERVER_IP}:{SERVER_PORT}/{filename}"
                    await msg.edit_text(f"📎 File too large for Telegram: {res['title']}\n\n🔗 Download: {link}\n\n⏰ Link expires in 1 hour")
                else:
                    with open(res["file"], 'rb') as f:
                        await update.message.reply_video(video=f, caption=res['title'], read_timeout=300, write_timeout=300)
                    shutil.rmtree(res["temp_dir"], ignore_errors=True)
                    await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

def main():
    print(f"🚀 Bot Starting | Token: {BOT_TOKEN[:10]}...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()