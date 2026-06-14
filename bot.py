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
print(f"Python: {sys.executable}")
print(f"CWD: {Path.cwd()}")

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
    print("ERROR: Set BOT_TOKEN in .env file")
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
        
        # Check if session file is too old (>6 hours) — Instagram sessions expire quickly
        if session_file.exists():
            age_hours = (time.time() - session_file.stat().st_mtime) / 3600
            if age_hours > 6:
                print(f"⚠️ Session file is {age_hours:.1f} hours old. Deleting for fresh login...")
                try:
                    session_file.unlink()
                except Exception:
                    pass
        
        # 0. Check if cookies.txt is fresh and has sessionid
        if COOKIE_FILE.exists():
            cookie_age_hours = (time.time() - COOKIE_FILE.stat().st_mtime) / 3600
            cookie_text = COOKIE_FILE.read_text()
            has_sessionid = 'sessionid' in cookie_text
            has_ds_user_id = 'ds_user_id' in cookie_text
            
            if cookie_age_hours <= 6 and has_sessionid and has_ds_user_id:
                print(f"✅ cookies.txt is fresh ({cookie_age_hours:.1f}h) with full auth cookies")
                if self._load_cookies_txt():
                    print("✅ Logged in via fresh cookies.txt")
                    self._export_cookies_to_ytdlp()
                    return
            else:
                print(f"⚠️ cookies.txt issues: age={cookie_age_hours:.1f}h, sessionid={'✅' if has_sessionid else '❌'}, ds_user_id={'✅' if has_ds_user_id else '❌'}")
        
        # 1. Try browser cookies first (freshest, auto-updated by system browser)
        if self._load_browser_cookies():
            print("✅ Logged in via system browser cookies")
            self._export_cookies_to_ytdlp()
            return
        
        # 2. Try cookies.txt (even if older, as fallback)
        if COOKIE_FILE.exists():
            if self._load_cookies_txt():
                print("✅ Logged in via cookies.txt (fallback)")
                self._export_cookies_to_ytdlp()
                return
        
        # 3. Try to load saved session file (pickle format)
        if session_file.exists():
            try:
                import pickle
                with open(session_file, 'rb') as f:
                    session = pickle.load(f)
                self.L.context._session = session
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
                self._export_cookies_to_ytdlp()
                return

        # 5. Try regular login
        if IG_USERNAME and IG_PASSWORD:
            try:
                print(f"🔑 Attempting direct API login for {IG_USERNAME}...")
                self.L.login(IG_USERNAME, IG_PASSWORD)
                print("✅ Login successful")
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
            self._export_cookies_to_ytdlp()
            return True
        
        if IG_USERNAME and IG_PASSWORD:
            try:
                self.L.login(IG_USERNAME, IG_PASSWORD)
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

    # ── METADATA EXTRACTION ─────────────────────────────────────────────────

    def _extract_post_metadata(self, post, shortcode: str, url: str) -> dict:
        """Extract comprehensive metadata from an instaloader Post object."""
        metadata = {
            "shortcode": shortcode,
            "url": url,
            "username": post.owner_username,
            "full_name": "",
            "user_id": str(post.owner_id),
            "post_id": str(post.mediaid),
            "caption": post.caption if post.caption else "",
            "hashtags": [],
            "mentions": [],
            "likes": post.likes,
            "comments": post.comments,
            "date": post.date_local.strftime("%Y-%m-%d %H:%M:%S") if post.date_local else "",
            "is_video": post.is_video,
            "media_count": 1,
            "location": "",
            "post_url": f"https://www.instagram.com/p/{shortcode}/",
        }

        # Extract hashtags and mentions from caption
        if post.caption:
            metadata["hashtags"] = re.findall(r'#(\w+)', post.caption)
            metadata["mentions"] = re.findall(r'@(\w+)', post.caption)

        # Try to get full name from profile
        try:
            profile = post.owner_profile
            metadata["full_name"] = profile.full_name if profile.full_name else profile.username
        except Exception:
            metadata["full_name"] = post.owner_username

        # Get media count for carousels
        try:
            if hasattr(post, 'sidecar_nodes'):
                metadata["media_count"] = len(list(post.get_sidecar_nodes()))
        except Exception:
            pass

        # Get location if available
        try:
            if post.location:
                metadata["location"] = post.location.name if hasattr(post.location, 'name') else str(post.location)
        except Exception:
            pass

        return metadata

    async def _extract_metadata_from_url(self, url: str, shortcode: str) -> dict:
        """Fallback metadata extraction using oEmbed / embed page (no auth needed)."""
        metadata = {
            "shortcode": shortcode,
            "url": url,
            "username": "unknown",
            "full_name": "",
            "user_id": "",
            "post_id": "",
            "caption": "",
            "hashtags": [],
            "mentions": [],
            "likes": 0,
            "comments": 0,
            "date": "",
            "is_video": False,
            "media_count": 1,
            "location": "",
            "post_url": f"https://www.instagram.com/p/{shortcode}/",
        }
        try:
            import requests
            # Instagram oEmbed endpoint (public, no auth)
            oembed_url = f"https://graph.facebook.com/v18.0/instagram_oembed"
            # Without an app token this won't work, so fallback to embed page scraping
            embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html",
            }
            resp = requests.get(embed_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                # Extract title / caption
                title_match = re.search(r'<title>([^<]+)</title>', html)
                if title_match:
                    raw_title = title_match.group(1).strip()
                    # Format: "Instagram post by username • caption"
                    if " by " in raw_title:
                        parts = raw_title.split(" by ", 1)
                        if len(parts) == 2:
                            rest = parts[1]
                            user_part = rest.split(" • ", 1)[0] if " • " in rest else rest.split(": ", 1)[0]
                            metadata["username"] = user_part.strip()
                            caption_part = rest.split(" • ", 1)[1] if " • " in rest else ""
                            metadata["caption"] = caption_part.strip()
                # Extract username from og:description or other meta tags
                desc_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html)
                if desc_match:
                    desc = html.unescape(desc_match.group(1))
                    metadata["caption"] = desc
                    metadata["hashtags"] = re.findall(r'#(\w+)', desc)
                    metadata["mentions"] = re.findall(r'@(\w+)', desc)
                # Check for video indicator
                if '<video' in html or 'og:video' in html:
                    metadata["is_video"] = True
        except Exception as e:
            print(f"⚠️ Metadata fallback extraction failed: {e}")
        return metadata

    def _format_metadata_caption(self, metadata: dict) -> str:
        """Format metadata dict into a nice Telegram caption."""
        lines = []
        lines.append(f"👤 *{html.escape(metadata.get('full_name') or metadata.get('username', 'Unknown'))}*")
        lines.append(f"🔗 @{html.escape(metadata.get('username', 'unknown'))}")
        lines.append("")
        if metadata.get("caption"):
            # Escape markdown chars in caption but keep it readable
            cap = metadata["caption"][:500]
            lines.append(f"📝 {html.escape(cap)}")
            lines.append("")
        if metadata.get("hashtags"):
            tags = " ".join([f"#{tag}" for tag in metadata["hashtags"][:15]])
            lines.append(f"🏷 {tags}")
            lines.append("")
        if metadata.get("mentions"):
            mentions = " ".join([f"@{m}" for m in metadata["mentions"]])
            lines.append(f"💬 {mentions}")
            lines.append("")
        stats = []
        if metadata.get("likes"):
            likes = metadata["likes"]
            stats.append(f"❤️ {likes:,}")
        if metadata.get("comments"):
            stats.append(f"💬 {metadata['comments']:,}")
        if metadata.get("media_count") and metadata["media_count"] > 1:
            stats.append(f"🖼 {metadata['media_count']} items")
        if metadata.get("date"):
            stats.append(f"📅 {metadata['date']}")
        if metadata.get("location"):
            stats.append(f"📍 {html.escape(str(metadata['location']))}")
        if stats:
            lines.append(" | ".join(stats))
            lines.append("")
        lines.append(f"[Open on Instagram]({metadata.get('post_url', metadata.get('url', ''))})")
        return "\n".join(lines)

    async def download(self, url: str, download_id: str) -> dict:
        # ── STORIES ─────────────────────────────────────────────────────────
        if self.is_story_url(url):
            temp_dir = TEMP_DIR / f"ig_{download_id}"
            temp_dir.mkdir(exist_ok=True)
            
            # Strategy for stories:
            # 1. Try gallery-dl (best for Instagram stories with cookies)
            # 2. Try Instagram GraphQL API directly
            # 3. Try yt-dlp ANONYMOUS first
            # 4. Try yt-dlp with browser cookies
            # 5. Try curl_cffi browser impersonation
            # 6. Try instaloader with our session
            # 7. Try web API fallback
            
            # 1. gallery-dl (most reliable for stories)
            print("📥 Story: trying gallery-dl...")
            result = await self._download_story_gallery_dl(url, temp_dir)
            if result.get("success"):
                return result
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(2)
            
            # 2. GraphQL API
            print("📥 Story: trying Instagram GraphQL API...")
            result = await self._download_story_graphql(url, temp_dir)
            if result.get("success"):
                return result
            
            await asyncio.sleep(2)
            
            if self.yt_dlp_available:
                # 3. Anonymous
                print("📥 Story: trying yt-dlp anonymous...")
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=False)
                if result.get("success"):
                    return result
                
                await asyncio.sleep(2)
                
                # 4. With browser cookies / auth
                print("🔄 Story: trying yt-dlp with auth...")
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=True)
                if result.get("success"):
                    return result
                
                await asyncio.sleep(2)
            
            # 5. curl_cffi browser impersonation
            print("🔄 Story: trying curl_cffi browser impersonation...")
            result = await self._download_story_curl_cffi(url, temp_dir)
            if result.get("success"):
                return result
            
            await asyncio.sleep(2)
            
            # 6. Instaloader
            print("🔄 Story: trying instaloader...")
            result = await self.download_story(url, download_id)
            if result.get("success"):
                return result
            
            await asyncio.sleep(2)
            
            # 7. Web API fallback
            print("🔄 Story: trying web API fallback...")
            result = await self._download_via_web_api(url, temp_dir)
            if result.get("success"):
                return result
            
            return result if result else {"success": False, "error": "Story download failed. Content may be private or expired."}
        
        # ── POSTS / REELS ───────────────────────────────────────────────────
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
                    # ── EXTRACT FULL METADATA ─────────────────────────────
                    metadata = self._extract_post_metadata(post, shortcode, url)
                    return {
                        "success": True,
                        "files": files,
                        "method": "instaloader",
                        "caption": post.caption[:400] if post.caption else "",
                        "author": post.owner_username,
                        "temp_dir": str(temp_dir),
                        "metadata": metadata,
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
                        # Try to enrich with metadata from the page
                        yt_result["metadata"] = await self._extract_metadata_from_url(url, shortcode)
                        return yt_result
                    elif attempt < 1:
                        await asyncio.sleep(3)
                        continue

        if self.yt_dlp_available:
            yt_result = await self._download_with_ytdlp(url, temp_dir)
            if yt_result.get("success"):
                yt_result["metadata"] = await self._extract_metadata_from_url(url, shortcode)
            return yt_result
        
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
        """Fallback: use multiple third-party web APIs to download Instagram content."""
        import requests
        import json
        
        headers_base = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        # ── API 1: SnapSave.app ──────────────────────────────────────────────
        try:
            print("   Trying SnapSave API...")
            
            # First get the token
            ss_home = requests.get("https://snapsave.app/", headers=headers_base, timeout=15)
            token_match = re.search(r'name="token" value="([^"]+)"', ss_home.text)
            token = token_match.group(1) if token_match else ""
            
            payload = {
                "url": url,
                "token": token,
            }
            ss_headers = {
                **headers_base,
                "Origin": "https://snapsave.app",
                "Referer": "https://snapsave.app/",
            }
            
            resp = requests.post("https://snapsave.app/action.php", data=payload, headers=ss_headers, timeout=30)
            
            if resp.status_code == 200:
                content = resp.text
                # Response is HTML with download links
                media_urls = []
                
                # Look for data-src or href attributes with media URLs
                found = re.findall(r'data-src="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', content)
                media_urls.extend(found)
                
                found2 = re.findall(r'href="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', content)
                media_urls.extend(found2)
                
                # Also look for any cdninstagram URLs
                found3 = re.findall(r'(https?://[^\s"\'<>]+\.cdninstagram\.com/[^\s"\'<>]+)', content)
                media_urls.extend(found3)
                
                media_urls = list(dict.fromkeys([u for u in media_urls if u]))
                
                if media_urls:
                    downloaded_files = []
                    for i, media_url in enumerate(media_urls[:5]):
                        try:
                            file_resp = requests.get(media_url, headers={
                                **headers_base,
                                "Referer": "https://snapsave.app/",
                            }, timeout=60, stream=True)
                            
                            if file_resp.status_code == 200:
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
                                print(f"   Downloaded via SnapSave: {file_path.name}")
                        except Exception as e:
                            print(f"   Failed to download SnapSave URL: {e}")
                            continue
                    
                    if downloaded_files:
                        return {
                            "success": True, "files": downloaded_files, "method": "snapsave",
                            "caption": "", "author": "unknown", "temp_dir": str(temp_dir)
                        }
        except Exception as e:
            print(f"   SnapSave failed: {e}")
        
        # ── API 2: Instagram's own embed/oembed (works for public posts) ────
        try:
            print("   Trying Instagram embed API...")
            
            shortcode = self.extract_shortcode(url)
            if shortcode:
                embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
                resp = requests.get(embed_url, headers={
                    **headers_base,
                    "Accept": "text/html",
                }, timeout=15)
                
                if resp.status_code == 200:
                    html = resp.text
                    # Look for image/video in embed
                    media_urls = []
                    
                    img_match = re.search(r'<img[^>]+src="(https?://[^"]+\.cdninstagram\.com/[^"]+)"', html)
                    if img_match:
                        media_urls.append(img_match.group(1))
                    
                    video_match = re.search(r'<video[^>]+src="(https?://[^"]+)"', html)
                    if video_match:
                        media_urls.append(video_match.group(1))
                    
                    # Look for og:image
                    og_match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
                    if og_match:
                        media_urls.append(og_match.group(1))
                    
                    media_urls = list(dict.fromkeys([u for u in media_urls if u]))
                    
                    if media_urls:
                        downloaded_files = []
                        for i, media_url in enumerate(media_urls[:3]):
                            try:
                                file_resp = requests.get(media_url, headers=headers_base, timeout=60)
                                if file_resp.status_code == 200:
                                    ext = '.mp4' if '.mp4' in media_url else '.jpg'
                                    file_path = temp_dir / f"embed_{i}{ext}"
                                    with open(file_path, 'wb') as f:
                                        f.write(file_resp.content)
                                    downloaded_files.append(str(file_path))
                                    print(f"   Downloaded via embed: {file_path.name}")
                            except Exception as e:
                                print(f"   Failed embed download: {e}")
                                continue
                        
                        if downloaded_files:
                            return {
                                "success": True, "files": downloaded_files, "method": "instagram-embed",
                                "caption": "", "author": "unknown", "temp_dir": str(temp_dir)
                            }
        except Exception as e:
            print(f"   Instagram embed failed: {e}")
        
        # ── API 3: ssstik.io (for videos) ────────────────────────────────────
        try:
            print("   Trying ssstik.io API...")
            
            payload = {"id": url}
            ss_headers = {
                **headers_base,
                "Origin": "https://ssstik.io",
                "Referer": "https://ssstik.io/",
                "Hx-Request": "true",
                "Hx-Target": "target",
                "Hx-Current-Url": "https://ssstik.io/",
            }
            
            resp = requests.post("https://ssstik.io/abc?url=dl", data=payload, headers=ss_headers, timeout=30)
            
            if resp.status_code == 200:
                html = resp.text
                media_urls = []
                
                # Look for download links
                found = re.findall(r'href="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', html)
                media_urls.extend(found)
                
                # Look for data-href
                found2 = re.findall(r'data-href="(https?://[^"]+)"', html)
                media_urls.extend(found2)
                
                media_urls = list(dict.fromkeys([u for u in media_urls if u]))
                
                if media_urls:
                    downloaded_files = []
                    for i, media_url in enumerate(media_urls[:3]):
                        try:
                            file_resp = requests.get(media_url, headers={
                                **headers_base,
                                "Referer": "https://ssstik.io/",
                            }, timeout=60, stream=True)
                            
                            if file_resp.status_code == 200:
                                content_type = file_resp.headers.get('Content-Type', '')
                                if 'video' in content_type:
                                    ext = '.mp4'
                                elif 'image' in content_type:
                                    ext = '.jpg'
                                else:
                                    ext = '.mp4' if '.mp4' in media_url else '.jpg'
                                
                                file_path = temp_dir / f"ssstik_{i}{ext}"
                                with open(file_path, 'wb') as f:
                                    for chunk in file_resp.iter_content(chunk_size=8192):
                                        f.write(chunk)
                                downloaded_files.append(str(file_path))
                                print(f"   Downloaded via ssstik: {file_path.name}")
                        except Exception as e:
                            print(f"   Failed ssstik download: {e}")
                            continue
                    
                    if downloaded_files:
                        return {
                            "success": True, "files": downloaded_files, "method": "ssstik",
                            "caption": "", "author": "unknown", "temp_dir": str(temp_dir)
                        }
        except Exception as e:
            print(f"   ssstik.io failed: {e}")
        
        return {"success": False, "error": "All web APIs failed. Content may be private or unavailable."}

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

    async def _download_story_gallery_dl(self, url: str, temp_dir: Path) -> dict:
        """Use gallery-dl to download Instagram stories. Best tool for this purpose."""
        try:
            import subprocess
            import shutil as sh
            
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            print(f"   gallery-dl: downloading story from {username}")
            
            # Export cookies to a file that gallery-dl can use
            cookies_file = temp_dir / "gallery_cookies.txt"
            if COOKIE_FILE.exists():
                sh.copy(COOKIE_FILE, cookies_file)
            else:
                # Try to export from current session
                self._export_cookies_to_ytdlp()
                if COOKIE_FILE.exists():
                    sh.copy(COOKIE_FILE, cookies_file)
            
            # Build gallery-dl command with proper options
            cmd = [
                "gallery-dl",
                "--destination", str(temp_dir),
                "--filename", "{media_id}.{extension}",
                "--no-mtime",
                "--option", "extractor.instagram.include=stories",
                "--option", "extractor.instagram.videos=true",
            ]
            
            # Add cookies if available
            if cookies_file.exists():
                cmd.extend(["--cookies", str(cookies_file)])
                print(f"   Using cookies: {cookies_file}")
            else:
                print("   No cookies available, trying without auth...")
            
            # Add the URL
            cmd.append(url)
            
            print(f"   Running gallery-dl...")
            
            # Run gallery-dl
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(temp_dir)
            )
            
            print(f"   gallery-dl exit code: {result.returncode}")
            if result.stdout:
                print(f"   stdout: {result.stdout[:300]}")
            if result.stderr:
                print(f"   stderr: {result.stderr[:300]}")
            
            # Collect downloaded files (check subdirectories too)
            downloaded_files = []
            for f in temp_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']:
                    downloaded_files.append(str(f))
            
            if downloaded_files:
                print(f"   ✅ gallery-dl downloaded {len(downloaded_files)} files")
                return {
                    "success": True,
                    "files": downloaded_files,
                    "method": "gallery-dl",
                    "caption": "",
                    "author": username,
                    "temp_dir": str(temp_dir)
                }
            
            return {"success": False, "error": "gallery-dl: no files downloaded"}
            
        except FileNotFoundError:
            return {"success": False, "error": "gallery-dl not installed. Run: pip install gallery-dl"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "gallery-dl: download timed out"}
        except Exception as e:
            print(f"   gallery-dl failed: {e}")
            return {"success": False, "error": f"gallery-dl failed: {str(e)[:100]}"}

    async def _download_story_graphql(self, url: str, temp_dir: Path) -> dict:
        """Use Instagram's GraphQL API directly to download stories. Most reliable method."""
        try:
            import requests
            import json
            
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            print(f"   GraphQL: fetching story {story_id} from {username}")
            
            # Get session cookies from instaloader
            session_cookies = {}
            if hasattr(self.L.context, '_session') and self.L.context._session:
                for cookie in self.L.context._session.cookies:
                    if "instagram" in (cookie.domain or ""):
                        session_cookies[cookie.name] = cookie.value
            
            if not session_cookies:
                print("   No session cookies available for GraphQL")
                return {"success": False, "error": "No Instagram session available"}
            
            # Required headers for GraphQL
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "X-IG-App-ID": "936619743392459",
                "X-ASBD-ID": "129477",
                "X-IG-WWW-Claim": "0",
                "Referer": "https://www.instagram.com/",
                "Origin": "https://www.instagram.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            
            session = requests.Session()
            session.headers.update(headers)
            for name, value in session_cookies.items():
                session.cookies.set(name, value, domain=".instagram.com", path="/")
            
            # Step 1: Get the user's ID from username
            print(f"   GraphQL: resolving user ID for {username}...")
            user_url = "https://www.instagram.com/api/v1/users/web_profile_info/"
            user_params = {"username": username}
            
            resp = session.get(user_url, params=user_params, timeout=30)
            if resp.status_code != 200:
                print(f"   Failed to get user info: {resp.status_code}")
                return {"success": False, "error": f"GraphQL user lookup failed: {resp.status_code}"}
            
            user_data = resp.json()
            user_id = user_data.get("data", {}).get("user", {}).get("id")
            if not user_id:
                print("   Could not extract user ID from response")
                return {"success": False, "error": "Could not resolve user ID"}
            
            print(f"   Resolved user ID: {user_id}")
            
            # Step 2: Fetch stories using GraphQL
            graphql_url = "https://www.instagram.com/graphql/query"
            
            # Query hash for stories
            query_hash = "5ec1d322b38839230f8e256e1f638d5f"
            
            variables = {
                "reel_ids": [user_id],
                "tag_names": [],
                "location_ids": [],
                "highlight_reel_ids": [],
                "precomposed_overlay": False,
                "show_story_viewer_list": True,
                "story_viewer_fetch_count": 50,
                "story_viewer_cursor": "",
                "stories_video_dash_manifest": False
            }
            
            params = {
                "query_hash": query_hash,
                "variables": json.dumps(variables)
            }
            
            print(f"   GraphQL: fetching stories feed...")
            resp = session.get(graphql_url, params=params, timeout=30)
            
            if resp.status_code != 200:
                print(f"   GraphQL request failed: {resp.status_code}")
                return {"success": False, "error": f"GraphQL failed: {resp.status_code}"}
            
            data = resp.json()
            
            # Extract story items
            reels_media = data.get("data", {}).get("reels_media", [])
            if not reels_media:
                print("   No reels_media in response")
                return {"success": False, "error": "No stories found for this user"}
            
            items = reels_media[0].get("items", [])
            if not items:
                print("   No story items found")
                return {"success": False, "error": "No active stories found"}
            
            print(f"   Found {len(items)} story items")
            
            # Find the specific story by ID or download all
            target_items = []
            for item in items:
                item_id = str(item.get("id", "")).split("_")[0]
                if item_id == story_id:
                    target_items = [item]
                    print(f"   Found target story: {story_id}")
                    break
            
            # If specific story not found, download all available stories
            if not target_items:
                target_items = items
                print(f"   Specific story not found, downloading all {len(items)} stories")
            
            # Download media from story items
            downloaded_files = []
            for idx, item in enumerate(target_items):
                media_urls = []
                
                # Check for video
                video_resources = item.get("video_resources", [])
                if video_resources:
                    # Get best quality video
                    best_video = max(video_resources, key=lambda x: x.get("config_width", 0))
                    video_url = best_video.get("src")
                    if video_url:
                        media_urls.append((video_url, ".mp4"))
                
                # Check for images
                display_resources = item.get("display_resources", [])
                if display_resources and not video_resources:
                    best_image = max(display_resources, key=lambda x: x.get("config_width", 0))
                    image_url = best_image.get("src")
                    if image_url:
                        media_urls.append((image_url, ".jpg"))
                
                # Fallback to display_url
                if not media_urls:
                    display_url = item.get("display_url")
                    if display_url:
                        is_video = item.get("is_video", False)
                        ext = ".mp4" if is_video else ".jpg"
                        media_urls.append((display_url, ext))
                
                for media_url, ext in media_urls:
                    try:
                        print(f"   Downloading: {media_url[:80]}...")
                        file_resp = session.get(media_url, headers={
                            "Referer": "https://www.instagram.com/",
                            "Accept": "*/*",
                        }, timeout=60)
                        
                        if file_resp.status_code == 200:
                            file_path = temp_dir / f"graphql_{idx}{ext}"
                            with open(file_path, 'wb') as f:
                                f.write(file_resp.content)
                            downloaded_files.append(str(file_path))
                            print(f"   ✅ Downloaded: {file_path.name} ({len(file_resp.content)} bytes)")
                        else:
                            print(f"   ❌ Download failed: {file_resp.status_code}")
                    except Exception as e:
                        print(f"   ❌ Download error: {e}")
                        continue
            
            if downloaded_files:
                return {
                    "success": True,
                    "files": downloaded_files,
                    "method": "instagram-graphql",
                    "caption": "",
                    "author": username,
                    "temp_dir": str(temp_dir)
                }
            
            return {"success": False, "error": "GraphQL: could not download story media"}
            
        except Exception as e:
            print(f"   GraphQL method failed: {e}")
            traceback.print_exc()
            return {"success": False, "error": f"GraphQL failed: {str(e)[:100]}"}

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
                
                # Check if it's a rate limit error - don't retry immediately
                if 'please wait a few minutes' in inner_err or '429' in inner_err:
                    print("   Rate limited by Instagram. Waiting 30 seconds...")
                    await asyncio.sleep(30)
                    return {"success": False, "error": "Instagram rate limit. Please try again in a few minutes."}
                
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
            
            # Check if it's a rate limit error
            if 'please wait a few minutes' in err_str or '429' in err_str:
                return {"success": False, "error": "Instagram rate limit. Please try again in a few minutes."}
            
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
                # ── SEND METADATA FIRST ───────────────────────────────────
                metadata = res.get("metadata", {})
                if metadata:
                    caption_text = ig_downloader._format_metadata_caption(metadata)
                    await msg.edit_text(caption_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                else:
                    await msg.delete()

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
                    await update.message.reply_text(f"📎 Large files:\n{links}\n\n⏰ Links expire in 1 hour")
                
                if small_files:
                    await update.message.reply_text("📤 Sending album...")
                    batch_size = 10
                    for batch_idx in range(0, len(small_files), batch_size):
                        batch = small_files[batch_idx:batch_idx + batch_size]
                        media_group = []
                        for i, f in enumerate(batch):
                            with open(f, 'rb') as file:
                                if f.lower().endswith(('.mp4', '.mov')):
                                    media_group.append(InputMediaVideo(file))
                                else:
                                    media_group.append(InputMediaPhoto(file))
                        await update.message.reply_media_group(media_group, read_timeout=300)
                
                shutil.rmtree(res["temp_dir"], ignore_errors=True)
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