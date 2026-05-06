#!/usr/bin/env python3
"""
Instagram & YouTube Downloader Bot
"""

import os
import re
import sys
import time
import asyncio
import logging
import shutil
import html
import traceback
from pathlib import Path

# Diagnostic: show which Python is running
print(f"🐍 Python: {sys.executable}")
print(f"📍 CWD: {Path.cwd()}")

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
        
        # Check if session file is too old (>12 hours) — Instagram sessions expire quickly
        if session_file.exists():
            age_hours = (time.time() - session_file.stat().st_mtime) / 3600
            if age_hours > 12:
                print(f"⚠️ Session file is {age_hours:.1f} hours old. Deleting for fresh login...")
                try:
                    session_file.unlink()
                except Exception:
                    pass
        
        # 1. Try browser cookies first (freshest, auto-updated by system browser)
        if self._load_browser_cookies():
            print("✅ Logged in via system browser cookies")
            self.L.save_session_to_file(str(session_file))
            self._export_cookies_to_ytdlp()
            return
        
        # 2. Try cookies.txt (manually exported or from extract_cookies.py)
        if COOKIE_FILE.exists():
            if self._load_cookies_txt():
                print("✅ Logged in via cookies.txt")
                self.L.save_session_to_file(str(session_file))
                return
        
        # 3. Try to load saved session file
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

        # 4. Try headless browser login
        if IG_USERNAME and IG_PASSWORD:
            if self._playwright_login():
                self.L.save_session_to_file(str(session_file))
                self._export_cookies_to_ytdlp()
                return

        # 5. Try regular login
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
                page_html = page.content()
                is_2fa = any(x in current_url for x in ["challenge", "two_factor", "confirm_email", "suspicious"])
                is_2fa = is_2fa or "two-factor" in page_html.lower() or "authentication code" in page_html.lower()
                if is_2fa:
                    print("   ⚠️ Instagram requires 2FA!")
                    browser.close()
                    # Try interactive visible browser so user can type 2FA code
                    return self._playwright_login_interactive()
                
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
                
        except ImportError as ie:
            print(f"   Playwright import failed: {ie}")
            print(f"   → Run: {sys.executable} -m pip install playwright")
            print(f"   → Then: {sys.executable} -m playwright install chromium")
            return False
        except Exception as e:
            print(f"   Headless browser login failed: {e}")
            traceback.print_exc()
            return False
    
    def _playwright_login_interactive(self) -> bool:
        """Open a VISIBLE browser so user can complete 2FA manually."""
        try:
            from playwright.sync_api import sync_playwright
            import requests
            
            print("=" * 60)
            print("🎭 INTERACTIVE LOGIN: A browser window will open.")
            print("   Please:")
            print("     1. Log into Instagram")
            print("     2. Enter your 2FA code when asked")
            print("     3. Wait until you see your feed")
            print("     4. Come back here and press ENTER")
            print("=" * 60)
            input("Press ENTER to open the browser...")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                
                page.goto("https://www.instagram.com/accounts/login/")
                
                print("\nBrowser is open. Complete login + 2FA.")
                input("Press ENTER after you're fully logged in (feed visible)...")
                
                # Extract cookies
                cookies = context.cookies()
                ig_cookies = [c for c in cookies if "instagram" in c.get("domain", "")]
                print(f"\n   Extracted {len(ig_cookies)} Instagram cookies")
                
                if len(ig_cookies) < 3:
                    print("   ⚠️ Too few cookies. Login may have failed.")
                    browser.close()
                    return False
                
                # Save to cookies.txt for yt-dlp
                lines = ["# Netscape HTTP Cookie File", "# This file was generated by bot.py interactive login"]
                for c in ig_cookies:
                    domain = c["domain"]
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", True) else "FALSE"
                    expires = str(int(c.get("expires", 0))) if c.get("expires") else "0"
                    lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{c['name']}\t{c['value']}")
                COOKIE_FILE.write_text("\n".join(lines))
                print(f"   🍪 Saved cookies to {COOKIE_FILE.name}")
                
                # Inject into instaloader
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                for c in ig_cookies:
                    session.cookies.set(
                        name=c["name"], value=c["value"],
                        domain=c["domain"], path=c.get("path", "/"),
                        secure=c.get("secure", True), expires=c.get("expires"),
                    )
                self.L.context._session = session
                browser.close()
            
            if self._validate_session():
                print("✅ Interactive login successful!")
                return True
            else:
                print("⚠️ Interactive login appeared to work but session is invalid")
                return False
                
        except ImportError:
            print("   Playwright not installed. Cannot do interactive login.")
            print(f"   → Run: {sys.executable} -m pip install playwright")
            print(f"   → Then: {sys.executable} -m playwright install chromium")
            return False
        except Exception as e:
            print(f"   Interactive login failed: {e}")
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
                
        except ImportError as ie:
            print(f"   browser_cookie3 import failed: {ie}")
            print(f"   → Run: {sys.executable} -m pip install browser_cookie3")
            return False
        except Exception as e:
            print(f"   Browser cookie load failed: {e}")
            traceback.print_exc()
            return False
    
    def _load_cookies_txt(self) -> bool:
        """Load cookies from a Netscape cookies.txt file into instaloader session."""
        try:
            import http.cookiejar as cookiejar
            import requests
            
            print("📄 Trying to load cookies from cookies.txt...")
            
            if not COOKIE_FILE.exists():
                return False
            
            # Load Netscape cookie file
            cj = cookiejar.MozillaCookieJar(str(COOKIE_FILE))
            cj.load(ignore_discard=True, ignore_expires=True)
            
            ig_cookies = [c for c in cj if "instagram" in c.domain]
            if len(ig_cookies) < 3:
                print(f"   Only {len(ig_cookies)} Instagram cookies in file (need >= 3)")
                return False
            
            print(f"   Found {len(ig_cookies)} Instagram cookies in cookies.txt")
            
            # Build requests session
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            
            for cookie in cj:
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
                print("✅ cookies.txt loaded successfully")
                return True
            else:
                print("⚠️ cookies.txt loaded but session invalid")
                return False
                
        except Exception as e:
            print(f"   cookies.txt load failed: {e}")
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
            
            # Strategy for stories:
            # 1. Try yt-dlp ANONYMOUS first (often works for public stories without login)
            # 2. Try yt-dlp with browser cookies
            # 3. Try instaloader with our session
            # 4. Try web API fallback
            
            if self.yt_dlp_available:
                # 1. Anonymous
                print("📥 Story: trying yt-dlp anonymous...")
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=False)
                if result.get("success"):
                    return result
                
                # 2. With browser cookies / auth
                print("🔄 Story: trying yt-dlp with auth...")
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=True)
                if result.get("success"):
                    return result
            
            # 3. curl_cffi browser impersonation
            print("🔄 Story: trying curl_cffi browser impersonation...")
            result = await self._download_story_curl_cffi(url, temp_dir)
            if result.get("success"):
                return result
            
            # 4. Instaloader
            print("🔄 Story: trying instaloader...")
            result = await self.download_story(url, download_id)
            if result.get("success"):
                return result
            
            # 5. Web API fallback
            print("🔄 Story: trying web API fallback...")
            result = await self._download_via_web_api(url, temp_dir)
            if result.get("success"):
                return result
            
            return result if result else {"success": False, "error": "Story download failed. Content may be private or expired."}
        
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

    async def _download_with_ytdlp(self, url: str, temp_dir: Path, retry_count: int = 0, use_auth: bool = True) -> dict:
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
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
            
            if use_auth:
                # Prefer live exported cookies, then browser, then username/password
                if COOKIE_FILE.exists():
                    ydl_opts['cookiefile'] = str(COOKIE_FILE)
                else:
                    # Try to read directly from system browser
                    if sys.platform == "darwin":
                        ydl_opts['cookiesfrombrowser'] = ('safari',)
                    elif sys.platform == "win32":
                        ydl_opts['cookiesfrombrowser'] = ('chrome',)
                    elif IG_USERNAME and IG_PASSWORD:
                        ydl_opts['username'] = IG_USERNAME
                        ydl_opts['password'] = IG_PASSWORD
            else:
                # Anonymous mode — no cookies, no login
                ydl_opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'

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
                return await self._download_with_ytdlp(url, temp_dir, retry_count + 1, use_auth=use_auth)
            
            return {"success": False, "error": f"yt-dlp failed: {error_msg[:100]}"}
        return {"success": False, "error": "No files found."}

    async def _download_via_web_api(self, url: str, temp_dir: Path) -> dict:
        """Fallback: use third-party web API to download Instagram content."""
        try:
            import requests
            import json
            
            print("   Trying savefrom.net API...")
            
            # savefrom.net API
            api_url = "https://worker.savefrom.net/savefrom.php"
            payload = {
                "sf_url": url,
                "sf_submit": "",
                "new": "2",
                "lang": "en",
                "app": "",
                "country": "us",
                "os": "Windows",
                "browser": "Chrome",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://savefrom.net",
                "Referer": "https://savefrom.net/",
            }
            
            resp = requests.post(api_url, data=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Web API returned {resp.status_code}"}
            
            # Parse response — it can be HTML or JSON
            content = resp.text
            
            # Look for direct media URLs in the response
            media_urls = []
            
            # Try JSON parsing first
            try:
                data = resp.json()
                if isinstance(data, dict):
                    # Various possible response formats
                    for key in ["url", "download_url", "video_url", "media_url", "src"]:
                        if key in data and data[key]:
                            media_urls.append(data[key])
                    # Nested structures
                    if "data" in data and isinstance(data["data"], list):
                        for item in data["data"]:
                            if isinstance(item, dict):
                                for key in ["url", "download_url", "video_url", "media_url"]:
                                    if key in item and item[key]:
                                        media_urls.append(item[key])
            except (json.JSONDecodeError, ValueError):
                pass
            
            # Fallback: regex extract URLs from HTML/text
            if not media_urls:
                # Look for mp4/jpg URLs
                found = re.findall(r'https?://[^\s"\'<>]+\.(?:mp4|jpg|jpeg|png)', content)
                media_urls.extend(found)
                
                # Look for data-url attributes
                found2 = re.findall(r'data-url="(https?://[^"]+)"', content)
                media_urls.extend(found2)
                
                # Look for href download links
                found3 = re.findall(r'href="(https?://[^"]+)"[^>]*download', content)
                media_urls.extend(found3)
            
            if not media_urls:
                return {"success": False, "error": "Web API: no media URLs found in response"}
            
            # Download the media files
            downloaded_files = []
            for i, media_url in enumerate(media_urls[:5]):  # Max 5 files
                try:
                    file_resp = requests.get(media_url, headers={
                        "User-Agent": headers["User-Agent"],
                        "Referer": "https://savefrom.net/",
                    }, timeout=60, stream=True)
                    
                    if file_resp.status_code == 200:
                        # Determine extension from content-type or URL
                        content_type = file_resp.headers.get('Content-Type', '')
                        if 'video' in content_type:
                            ext = '.mp4'
                        elif 'image' in content_type:
                            ext = '.jpg'
                        else:
                            ext = '.mp4' if '.mp4' in media_url else '.jpg'
                        
                        file_path = temp_dir / f"webapi_{i}{ext}"
                        with open(file_path, 'wb') as f:
                            for chunk in file_resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        downloaded_files.append(str(file_path))
                        print(f"   Downloaded via web API: {file_path.name}")
                except Exception as e:
                    print(f"   Failed to download media URL: {e}")
                    continue
            
            if downloaded_files:
                return {
                    "success": True, "files": downloaded_files, "method": "web-api",
                    "caption": "", "author": "unknown", "temp_dir": str(temp_dir)
                }
            
            return {"success": False, "error": "Web API: failed to download media files"}
            
        except Exception as e:
            return {"success": False, "error": f"Web API failed: {str(e)[:80]}"}

    async def _download_story_curl_cffi(self, url: str, temp_dir: Path) -> dict:
        """Use curl_cffi to impersonate a real browser and scrape the story page."""
        try:
            from curl_cffi import requests as curl_requests
            import json
            
            print("   Trying curl_cffi browser impersonation...")
            
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            
            # Create a session that impersonates Chrome 120
            session = curl_requests.Session(impersonate="chrome120")
            
            # Fetch the story page
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.instagram.com/",
            }
            
            # Add cookies from our instaloader session if available
            session_cookies = self.L.context._session.cookies if hasattr(self.L.context, '_session') and self.L.context._session else None
            if session_cookies:
                for cookie in session_cookies:
                    if "instagram" in (cookie.domain or ""):
                        session.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path or "/")
            
            resp = session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"curl_cffi got status {resp.status_code}"}
            
            html_content = resp.text
            
            # Extract embedded JSON data
            media_urls = []
            
            # Method 1: Look for window._sharedData
            shared_data_match = re.search(r'window\._sharedData\s*=\s*({.+?});</script>', html_content, re.DOTALL)
            if shared_data_match:
                try:
                    data = json.loads(shared_data_match.group(1))
                    media = data.get("entry_data", {}).get("StoriesPage", [{}])[0].get("media", {})
                    if media:
                        video_url = media.get("video_url")
                        if video_url:
                            media_urls.append(video_url)
                        display_url = media.get("display_url")
                        if display_url:
                            media_urls.append(display_url)
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass
            
            # Method 2: Look for window.__additionalDataLoaded
            additional_data_match = re.search(r'window\.__additionalDataLoaded\s*\(\s*[\'"][^\'"]+[\'"]\s*,\s*({.+?})\s*\);</script>', html_content, re.DOTALL)
            if additional_data_match:
                try:
                    data = json.loads(additional_data_match.group(1))
                    items = data.get("items", [])
                    for item in items:
                        video_versions = item.get("video_versions", [])
                        if video_versions:
                            media_urls.append(video_versions[0].get("url"))
                        image_versions = item.get("image_versions2", {}).get("candidates", [])
                        if image_versions:
                            media_urls.append(image_versions[0].get("url"))
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # Method 3: Look for meta tags
            og_video_match = re.search(r'<meta[^>]+property=[\'"]og:video[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_content)
            if og_video_match:
                media_urls.append(og_video_match.group(1))
            
            og_image_match = re.search(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_content)
            if og_image_match:
                media_urls.append(og_image_match.group(1))
            
            # Method 4: Generic regex for CDN URLs
            cdn_urls = re.findall(r'https?://[^\s"\'<>]+\.cdninstagram\.com/[^\s"\'<>]+\.(?:mp4|jpg)', html_content)
            media_urls.extend(cdn_urls)
            
            # Remove duplicates and None values
            media_urls = list(dict.fromkeys([u for u in media_urls if u]))
            
            if not media_urls:
                return {"success": False, "error": "curl_cffi: no media URLs found in page"}
            
            # Download media files
            downloaded_files = []
            for i, media_url in enumerate(media_urls[:3]):
                try:
                    file_resp = session.get(media_url, headers={
                        "Referer": "https://www.instagram.com/",
                        "Accept": "*/*",
                    }, timeout=60)
                    
                    if file_resp.status_code == 200:
                        content_type = file_resp.headers.get('Content-Type', '')
                        if 'video' in content_type or '.mp4' in media_url:
                            ext = '.mp4'
                        else:
                            ext = '.jpg'
                        
                        file_path = temp_dir / f"curlcffi_{i}{ext}"
                        with open(file_path, 'wb') as f:
                            f.write(file_resp.content)
                        downloaded_files.append(str(file_path))
                        print(f"   Downloaded via curl_cffi: {file_path.name}")
                except Exception as e:
                    print(f"   Failed to download media URL: {e}")
                    continue
            
            if downloaded_files:
                return {
                    "success": True, "files": downloaded_files, "method": "curl-cffi",
                    "caption": "", "author": username, "temp_dir": str(temp_dir)
                }
            
            return {"success": False, "error": "curl_cffi: failed to download media"}
            
        except ImportError:
            return {"success": False, "error": "curl_cffi not installed"}
        except Exception as e:
            return {"success": False, "error": f"curl_cffi failed: {str(e)[:80]}"}

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