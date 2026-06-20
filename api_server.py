#!/usr/bin/env python3
"""
Social Media Downloader API Server
- REST API: POST /download to queue a job
- WebSocket: /ws to receive real-time download progress + files
- Decoupled from Telegram — runs independently
- Graceful Instagram auth handling — no crash on failure
"""

import os
import sys
import re
import html
import time
import json
import uuid
import asyncio
import shutil
import traceback
import subprocess
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Fix Windows console Unicode output for emoji/log messages
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─── Configuration ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp_downloads"
TEMP_DIR.mkdir(exist_ok=True)
COOKIE_FILE = BASE_DIR / "cookies.txt"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SERVER_HOST = os.getenv("API_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("API_PORT", "8000"))
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

# ─── In-memory job store ───────────────────────────────────────────────────
jobs: Dict[str, dict] = {}
active_connections: List[WebSocket] = []

# ─── SQLite persistence ────────────────────────────────────────────────────
DB_FILE = BASE_DIR / "jobs.db"
CLEANUP_AGE_HOURS = int(os.getenv("CLEANUP_AGE_HOURS", "48"))

def _init_db():
    """Create the jobs table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                files TEXT,
                error TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()

def _row_to_job(row: tuple) -> dict:
    job_id, url, platform, status, progress, files_json, error, metadata_json, created_at, updated_at = row
    return {
        "job_id": job_id,
        "url": url,
        "platform": platform,
        "status": status,
        "progress": progress,
        "files": json.loads(files_json) if files_json else [],
        "error": error,
        "metadata": json.loads(metadata_json) if metadata_json else None,
        "created_at": created_at,
        "updated_at": updated_at,
    }

def _save_job_to_db(job: dict):
    """Upsert a job into SQLite."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("""
            INSERT INTO jobs (job_id, url, platform, status, progress, files, error, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status=excluded.status,
                progress=excluded.progress,
                files=excluded.files,
                error=excluded.error,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
        """, (
            job["job_id"],
            job["url"],
            job["platform"],
            job["status"],
            job.get("progress", 0),
            json.dumps(job.get("files", [])),
            job.get("error"),
            json.dumps(job.get("metadata")) if job.get("metadata") is not None else None,
            job.get("created_at", datetime.now().isoformat()),
            datetime.now().isoformat(),
        ))
        conn.commit()
    finally:
        conn.close()

def _load_jobs_from_db() -> Dict[str, dict]:
    """Load all non-stale jobs from SQLite on startup."""
    if not DB_FILE.exists():
        return {}
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return {row[0]: _row_to_job(row) for row in rows}
    finally:
        conn.close()

def _delete_job_from_db(job_id: str):
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()

def _cleanup_old_files():
    """Delete temp files and folders older than CLEANUP_AGE_HOURS."""
    cutoff = time.time() - (CLEANUP_AGE_HOURS * 3600)
    removed = 0
    if not TEMP_DIR.exists():
        return removed
    for item in TEMP_DIR.iterdir():
        try:
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
            elif item.is_dir():
                # Delete the whole dir if its newest file is older than cutoff
                newest = max(
                    (f.stat().st_mtime for f in item.rglob("*") if f.is_file()),
                    default=0
                )
                if newest < cutoff:
                    shutil.rmtree(item, ignore_errors=True)
                    removed += 1
        except Exception as e:
            print(f"⚠️ Cleanup error for {item}: {e}")
    if removed:
        print(f"🧹 Cleaned up {removed} old temp item(s)")
    return removed

def _cleanup_old_db_jobs():
    """Delete completed/failed job records older than CLEANUP_AGE_HOURS."""
    cutoff = (datetime.now() - timedelta(hours=CLEANUP_AGE_HOURS)).isoformat()
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'failed') AND updated_at < ?",
            (cutoff,)
        )
        conn.commit()
        if cur.rowcount:
            print(f"🗑️  Pruned {cur.rowcount} old job record(s) from SQLite")
        return cur.rowcount
    finally:
        conn.close()

def _recover_stale_jobs():
    """Mark any 'downloading' jobs as failed on startup (server crashed)."""
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='failed', error='Server restarted while job was running', updated_at=? WHERE status='downloading'",
            (datetime.now().isoformat(),)
        )
        conn.commit()
        if cur.rowcount:
            print(f"🔄 Recovered {cur.rowcount} stale job(s) marked as downloading")
        return cur.rowcount
    finally:
        conn.close()

async def _periodic_cleanup():
    """Run cleanup every hour in the background."""
    while True:
        try:
            await asyncio.sleep(3600)
            await asyncio.to_thread(_cleanup_old_files)
            await asyncio.to_thread(_cleanup_old_db_jobs)
        except Exception as e:
            print(f"⚠️ Periodic cleanup error: {e}")

async def _periodic_cookie_reload():
    """Check for refreshed cookies.txt every 5 minutes while server is running."""
    while True:
        try:
            await asyncio.sleep(300)
            if ig_downloader:
                await asyncio.to_thread(ig_downloader.reload_cookies_if_changed)
        except Exception as e:
            print(f"⚠️ Periodic cookie reload error: {e}")

# ─── Pydantic Models ───────────────────────────────────────────────────────
class DownloadRequest(BaseModel):
    url: str
    platform: Optional[str] = None  # auto-detect if not provided

class DownloadResponse(BaseModel):
    job_id: str
    status: str
    message: str

class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, downloading, completed, failed
    progress: int  # 0-100
    files: List[str]
    error: Optional[str] = None
    metadata: Optional[dict] = None

# ─── Graceful Instagram Loader ─────────────────────────────────────────────
class GracefulInstagramDownloader:
    """Instagram downloader that doesn't crash if auth fails.
    
    Mirrors the robust login + fallback logic from bot.py but is fully
    decoupled from Telegram and safe to run inside the API server.
    """
    
    def __init__(self):
        self.L = None
        self.yt_dlp_available = False
        self.auth_ok = False
        self.auth_error = None
        self.anonymous_mode = True
        self._init()
    
    def _init(self):
        try:
            import instaloader
            self.L = instaloader.Instaloader(
                download_pictures=True,
                download_videos=True,
                download_video_thumbnails=False,
                save_metadata=False,
                request_timeout=60
            )
        except ImportError as e:
            self.auth_error = f"instaloader not installed: {e}"
            print(f"⚠️ {self.auth_error}")
            return
        
        try:
            import yt_dlp
            self.yt_dlp_available = True
        except ImportError:
            pass
        
        # Attempt login but NEVER crash
        try:
            self._login()
        except Exception as e:
            self.auth_error = str(e)
            print(f"⚠️ Instagram auth failed (non-fatal): {e}")
            traceback.print_exc()
    
    def _login(self):
        """Try to login, but always return gracefully."""
        import requests
        
        session_file = BASE_DIR / f"session_{IG_USERNAME}"
        
        # Delete stale session file (>6 hours)
        if session_file.exists():
            age_hours = (time.time() - session_file.stat().st_mtime) / 3600
            if age_hours > 6:
                print(f"⚠️ Session file is {age_hours:.1f} hours old. Deleting...")
                try:
                    session_file.unlink()
                except Exception:
                    pass
        
        # 0. Fresh cookies.txt
        if COOKIE_FILE.exists():
            cookie_age_hours = (time.time() - COOKIE_FILE.stat().st_mtime) / 3600
            cookie_text = COOKIE_FILE.read_text(errors="ignore")
            has_sessionid = 'sessionid' in cookie_text
            has_ds_user_id = 'ds_user_id' in cookie_text
            
            if cookie_age_hours <= 6 and has_sessionid and has_ds_user_id:
                print(f"📄 cookies.txt is fresh ({cookie_age_hours:.1f}h) with full auth cookies")
                if self._load_cookies_txt():
                    if self._validate_session():
                        self.auth_ok = True
                        self.anonymous_mode = False
                        print("✅ Instagram: logged in via fresh cookies.txt")
                        return
            else:
                print(f"⚠️ cookies.txt issues: age={cookie_age_hours:.1f}h, sessionid={'✅' if has_sessionid else '❌'}, ds_user_id={'✅' if has_ds_user_id else '❌'}")
        
        # 1. Browser cookies
        try:
            if self._load_browser_cookies():
                if self._validate_session():
                    self.auth_ok = True
                    self.anonymous_mode = False
                    print("✅ Instagram: logged in via system browser cookies")
                    self._export_cookies()
                    return
        except Exception as e:
            print(f"   Browser cookies failed: {e}")
        
        # 2. Older cookies.txt fallback
        if COOKIE_FILE.exists():
            try:
                if self._load_cookies_txt():
                    if self._validate_session():
                        self.auth_ok = True
                        self.anonymous_mode = False
                        print("✅ Instagram: logged in via cookies.txt (fallback)")
                        return
            except Exception as e:
                print(f"   cookies.txt fallback failed: {e}")
        
        # 3. Saved session file
        if session_file.exists():
            try:
                import pickle
                with open(session_file, 'rb') as f:
                    session = pickle.load(f)
                self.L.context._session = session
                if self._validate_session():
                    self.auth_ok = True
                    self.anonymous_mode = False
                    print("✅ Instagram: loaded saved session")
                    return
                else:
                    print("⚠️ Session file loaded but validation failed (expired)")
            except Exception as e:
                print(f"   Session file failed: {e}")
        
        # 4. Playwright headless browser login
        if IG_USERNAME and IG_PASSWORD:
            try:
                if self._playwright_login():
                    self.auth_ok = True
                    self.anonymous_mode = False
                    self._export_cookies()
                    return
            except Exception as e:
                print(f"   Playwright login failed: {e}")
        
        # 5. Direct login
        if IG_USERNAME and IG_PASSWORD:
            try:
                self.L.login(IG_USERNAME, IG_PASSWORD)
                self.auth_ok = True
                self.anonymous_mode = False
                print("✅ Instagram: direct login successful")
                self._export_cookies()
                return
            except Exception as e:
                print(f"   Direct login failed: {e}")
        
        # 6. Anonymous mode
        self.auth_ok = False
        self.anonymous_mode = True
        print("⚠️ Instagram: running in ANONYMOUS mode (public posts only)")
    
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
                except Exception:
                    pass
                
                current_url = page.url
                if "instagram.com" in current_url and "login" not in current_url:
                    print("   Already logged in (redirected from login page)")
                else:
                    username_filled = False
                    for username_sel in ["input[name='username']", "input[aria-label='Phone number, username, or email']", "input[type='text']"]:
                        try:
                            page.locator(username_sel).first.fill(IG_USERNAME)
                            username_filled = True
                            break
                        except Exception:
                            continue
                    
                    password_filled = False
                    for pwd_sel in ["input[name='password']", "input[aria-label='Password']", "input[type='password']"]:
                        try:
                            page.locator(pwd_sel).first.fill(IG_PASSWORD)
                            password_filled = True
                            break
                        except Exception:
                            continue
                    
                    if not username_filled or not password_filled:
                        print("   Could not find login form fields.")
                        browser.close()
                        return False
                    
                    for btn_sel in ["button[type='submit']", "[role='button']:has-text('Log in')", "button:has-text('Log in')"]:
                        try:
                            page.locator(btn_sel).first.click()
                            break
                        except Exception:
                            continue
                    
                    page.wait_for_timeout(4000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                
                page.wait_for_timeout(2000)
                
                # Check for 2FA / challenge
                current_url = page.url
                page_html = page.content()
                is_2fa = any(x in current_url for x in ["challenge", "two_factor", "confirm_email", "suspicious"])
                is_2fa = is_2fa or "two-factor" in page_html.lower() or "authentication code" in page_html.lower()
                if is_2fa:
                    print("   ⚠️ Instagram requires 2FA!")
                    browser.close()
                    return False
                
                # Check logged-in indicators
                logged_in = False
                for indicator in ["a[href='/direct/inbox/']", "[aria-label='Direct']", "nav", "[role='navigation']", "svg[aria-label='Home']"]:
                    try:
                        if page.locator(indicator).first.is_visible(timeout=3000):
                            logged_in = True
                            break
                    except Exception:
                        continue
                
                if not logged_in and "login" in page.url:
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
                        except Exception:
                            continue
                    print("   ⚠️ Still on login page. Login may have failed.")
                    browser.close()
                    return False
                
                cookies = context.cookies()
                ig_cookies = [c for c in cookies if "instagram" in c.get("domain", "")]
                print(f"   Extracted {len(ig_cookies)} Instagram cookies from browser")
                
                if len(ig_cookies) < 3:
                    print("   ⚠️ Too few cookies extracted. Login may have failed.")
                    browser.close()
                    return False
                
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
    
    def _load_browser_cookies(self) -> bool:
        """Extract Instagram cookies from system browser and inject into instaloader session."""
        try:
            import browser_cookie3
            import requests
            
            print("🌐 Trying to load Instagram cookies from system browser...")
            
            if sys.platform == "darwin":
                browsers = [browser_cookie3.safari, browser_cookie3.chrome, browser_cookie3.firefox, browser_cookie3.edge]
            elif sys.platform == "win32":
                browsers = [browser_cookie3.chrome, browser_cookie3.edge, browser_cookie3.firefox]
            else:
                browsers = [browser_cookie3.chrome, browser_cookie3.firefox, browser_cookie3.edge]
            
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
            return True
                
        except ImportError as ie:
            print(f"   browser_cookie3 import failed: {ie}")
            print(f"   → Run: {sys.executable} -m pip install browser_cookie3")
            return False
        except Exception as e:
            print(f"   Browser cookie load failed: {e}")
            traceback.print_exc()
            return False
    
    def _load_cookies_txt(self) -> bool:
        try:
            import http.cookiejar as cookiejar
            import requests
            
            if not COOKIE_FILE.exists():
                return False
            
            cj = cookiejar.MozillaCookieJar(str(COOKIE_FILE))
            cj.load(ignore_discard=True, ignore_expires=True)
            
            ig_cookies = [c for c in cj if "instagram" in c.domain]
            if len(ig_cookies) < 3:
                print(f"   Only {len(ig_cookies)} Instagram cookies in file (need >= 3)")
                return False
            
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            
            for cookie in cj:
                if "instagram" in cookie.domain:
                    session.cookies.set(
                        name=cookie.name, value=cookie.value,
                        domain=cookie.domain, path=cookie.path or "/",
                        secure=cookie.secure, expires=cookie.expires,
                    )
            
            self.L.context._session = session
            return True
        except Exception:
            return False
    
    def _validate_session(self) -> bool:
        try:
            from instaloader import Profile
            session_cookies = self.L.context._session.cookies if hasattr(self.L.context, '_session') else None
            if session_cookies:
                has_sessionid = any(c.name == 'sessionid' for c in session_cookies)
                if not has_sessionid:
                    return False
            
            # Try to fetch the configured user's profile, or a known public profile as fallback
            target_username = IG_USERNAME if IG_USERNAME else "instagram"
            profile = Profile.from_username(self.L.context, target_username)
            _ = profile.userid
            return True
        except Exception:
            return False
    
    def _force_fresh_login(self):
        """Delete old session and force a brand new login."""
        session_file = BASE_DIR / f"session_{IG_USERNAME}"
        print("🔄 Forcing fresh login...")
        
        if session_file.exists():
            try:
                session_file.unlink()
                print(f"   Deleted old session file: {session_file.name}")
            except Exception as e:
                print(f"   Could not delete session file: {e}")
        
        if COOKIE_FILE.exists():
            try:
                COOKIE_FILE.unlink()
                print("   Deleted old cookies.txt")
            except Exception:
                pass
        
        if self._playwright_login():
            self.auth_ok = True
            self.anonymous_mode = False
            self._export_cookies()
            return True
        
        if IG_USERNAME and IG_PASSWORD:
            try:
                self.L.login(IG_USERNAME, IG_PASSWORD)
                self.auth_ok = True
                self.anonymous_mode = False
                print("✅ Fresh direct login successful")
                self._export_cookies()
                return True
            except Exception as e:
                print(f"❌ Fresh direct login failed: {e}")
        
        self.auth_ok = False
        return False
    
    def _export_cookies(self):
        try:
            import http.cookiejar as cookiejar
            session = self.L.context._session
            if not session or not session.cookies:
                return
            cj = cookiejar.MozillaCookieJar(str(COOKIE_FILE))
            for cookie in session.cookies:
                rest = {}
                if hasattr(cookie, '_rest') and cookie._rest and cookie._rest.get("HttpOnly"):
                    rest = {"HttpOnly": ""}
                c = cookiejar.Cookie(
                    version=0, name=cookie.name, value=cookie.value,
                    port=None, port_specified=False,
                    domain=cookie.domain if cookie.domain else ".instagram.com",
                    domain_specified=bool(cookie.domain),
                    domain_initial_dot=cookie.domain.startswith(".") if cookie.domain else True,
                    path=cookie.path if cookie.path else "/",
                    path_specified=bool(cookie.path),
                    secure=cookie.secure,
                    expires=cookie.expires if cookie.expires else None,
                    discard=False, comment=None, comment_url=None,
                    rest=rest, rfc2109=False,
                )
                cj.set_cookie(c)
            cj.save(ignore_discard=True, ignore_expires=True)
            print(f"🍪 Exported live session cookies to {COOKIE_FILE.name}")
        except Exception as e:
            print(f"⚠️ Cookie export failed: {e}")
    
    def reload_cookies_if_changed(self):
        """Reload cookies from cookies.txt if the file has been updated externally."""
        try:
            if not COOKIE_FILE.exists():
                return False
            mtime = COOKIE_FILE.stat().st_mtime
            if not hasattr(self, "_cookie_file_mtime"):
                self._cookie_file_mtime = mtime
                return False
            if mtime <= self._cookie_file_mtime:
                return False
            self._cookie_file_mtime = mtime
            
            print("🍪 cookies.txt changed externally, reloading...")
            if self._load_cookies_txt() and self._validate_session():
                self.auth_ok = True
                self.anonymous_mode = False
                self.auth_error = None
                print("✅ Instagram session reloaded from updated cookies.txt")
                return True
            else:
                print("⚠️ Reloaded cookies.txt but session is still invalid")
                return False
        except Exception as e:
            print(f"⚠️ Cookie reload error: {e}")
            return False
    
    # ── URL helpers ─────────────────────────────────────────────────────────
    def extract_shortcode(self, url: str) -> str:
        m = re.search(r'instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
        return m.group(1) if m else None
    
    def is_story_url(self, url: str):
        m = re.search(r'instagram\.com/stories/([^/]+)/(\d+)', url)
        if m:
            return (m.group(1), m.group(2))
        return None
    
    def _collect_files(self, temp_dir: Path) -> list:
        return [str(f) for f in temp_dir.iterdir() if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']]
    
    # ── Metadata extraction ─────────────────────────────────────────────────
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
        
        if post.caption:
            metadata["hashtags"] = re.findall(r'#(\w+)', post.caption)
            metadata["mentions"] = re.findall(r'@(\w+)', post.caption)
        
        try:
            profile = post.owner_profile
            metadata["full_name"] = profile.full_name if profile.full_name else profile.username
        except Exception:
            metadata["full_name"] = post.owner_username
        
        try:
            if hasattr(post, 'sidecar_nodes'):
                metadata["media_count"] = len(list(post.get_sidecar_nodes()))
        except Exception:
            pass
        
        try:
            if post.location:
                metadata["location"] = post.location.name if hasattr(post.location, 'name') else str(post.location)
        except Exception:
            pass
        
        return metadata
    
    async def _extract_metadata_from_url(self, url: str, shortcode: str) -> dict:
        """Fallback metadata extraction using embed page (no auth needed)."""
        import requests
        
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
            embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html",
            }
            resp = requests.get(embed_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                page_html = resp.text
                title_match = re.search(r'<title>([^<]+)</title>', page_html)
                if title_match:
                    raw_title = title_match.group(1).strip()
                    if " by " in raw_title:
                        parts = raw_title.split(" by ", 1)
                        if len(parts) == 2:
                            rest = parts[1]
                            user_part = rest.split(" • ", 1)[0] if " • " in rest else rest.split(": ", 1)[0]
                            metadata["username"] = user_part.strip()
                            caption_part = rest.split(" • ", 1)[1] if " • " in rest else ""
                            metadata["caption"] = caption_part.strip()
                desc_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', page_html)
                if desc_match:
                    desc = html.unescape(desc_match.group(1))
                    metadata["caption"] = desc
                    metadata["hashtags"] = re.findall(r'#(\w+)', desc)
                    metadata["mentions"] = re.findall(r'@(\w+)', desc)
                if '<video' in page_html or 'og:video' in page_html:
                    metadata["is_video"] = True
        except Exception as e:
            print(f"⚠️ Metadata fallback extraction failed: {e}")
        return metadata
    
    def _format_metadata_caption(self, metadata: dict) -> str:
        """Format metadata dict into a nice plain-text caption for API consumers."""
        lines = []
        lines.append(f"👤 {metadata.get('full_name') or metadata.get('username', 'Unknown')}")
        lines.append(f"🔗 @{metadata.get('username', 'unknown')}")
        lines.append("")
        if metadata.get("caption"):
            cap = metadata["caption"][:500]
            lines.append(f"📝 {cap}")
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
            stats.append(f"📍 {metadata['location']}")
        if stats:
            lines.append(" | ".join(stats))
            lines.append("")
        lines.append(f"Open on Instagram: {metadata.get('post_url', metadata.get('url', ''))}")
        return "\n".join(lines)
    
    # ── Main async download entrypoint ──────────────────────────────────────
    async def download(self, url: str, job_id: str, progress_callback=None) -> dict:
        """Download Instagram content. Reports progress via callback."""
        
        async def report(msg, pct):
            if progress_callback:
                await progress_callback({"type": "progress", "message": msg, "percent": pct})
        
        await report("Parsing URL...", 5)
        
        # ── STORIES ─────────────────────────────────────────────────────────
        story_info = self.is_story_url(url)
        if story_info:
            if not self.auth_ok and not self.anonymous_mode:
                return {"success": False, "error": "Instagram stories require authentication. Auth failed or not configured."}
            
            temp_dir = TEMP_DIR / f"ig_{job_id}"
            temp_dir.mkdir(exist_ok=True)
            username, story_id = story_info
            
            await report(f"Downloading story from {username}...", 15)
            
            # 1. gallery-dl
            await report("Trying gallery-dl...", 20)
            result = await self._download_story_gallery_dl(url, temp_dir)
            if result.get("success"):
                await report("Story download complete", 100)
                return result
            
            await asyncio.sleep(2)
            
            # 2. GraphQL
            await report("Trying Instagram GraphQL API...", 35)
            result = await self._download_story_graphql(url, temp_dir)
            if result.get("success"):
                await report("Story download complete", 100)
                return result
            
            await asyncio.sleep(2)
            
            # 3. yt-dlp anonymous
            if self.yt_dlp_available:
                await report("Trying yt-dlp anonymous...", 50)
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=False)
                if result.get("success"):
                    await report("Story download complete", 100)
                    return result
                
                await asyncio.sleep(2)
                
                # 4. yt-dlp with auth
                await report("Trying yt-dlp with auth...", 60)
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=True)
                if result.get("success"):
                    await report("Story download complete", 100)
                    return result
                
                await asyncio.sleep(2)
            
            # 5. curl_cffi
            await report("Trying curl_cffi browser impersonation...", 70)
            result = await self._download_story_curl_cffi(url, temp_dir)
            if result.get("success"):
                await report("Story download complete", 100)
                return result
            
            await asyncio.sleep(2)
            
            # 6. instaloader
            await report("Trying instaloader...", 80)
            result = await self._download_story_instaloader(url, job_id)
            if result.get("success"):
                await report("Story download complete", 100)
                return result
            
            await asyncio.sleep(2)
            
            # 7. web API fallback
            await report("Trying web API fallback...", 90)
            result = await self._download_via_web_api(url, temp_dir)
            if result.get("success"):
                await report("Story download complete", 100)
                return result
            
            return {"success": False, "error": "Story download failed. Content may be private, expired, or requires login."}
        
        # ── POSTS / REELS ───────────────────────────────────────────────────
        shortcode = self.extract_shortcode(url)
        if not shortcode:
            return {"success": False, "error": "Invalid Instagram URL"}
        
        temp_dir = TEMP_DIR / f"ig_{job_id}"
        temp_dir.mkdir(exist_ok=True)
        
        await report(f"Fetching post {shortcode}...", 10)
        
        # Try instaloader first (if auth ok)
        if self.auth_ok or self.anonymous_mode:
            for attempt in range(2):
                if attempt > 0:
                    await report(f"Retry attempt {attempt + 1}...", 15)
                    await asyncio.sleep(2)
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    temp_dir.mkdir(exist_ok=True)
                
                try:
                    from instaloader import Post
                    post = Post.from_shortcode(self.L.context, shortcode)
                    self.L.dirname_pattern = str(temp_dir)
                    self.L.download_post(post, target=shortcode)
                    
                    files = self._collect_files(temp_dir)
                    if files:
                        await report("Download complete", 100)
                        metadata = self._extract_post_metadata(post, shortcode, url)
                        return {
                            "success": True,
                            "files": files,
                            "method": "instaloader",
                            "temp_dir": str(temp_dir),
                            "metadata": metadata,
                            "caption": self._format_metadata_caption(metadata),
                        }
                except Exception as e:
                    error_str = str(e).lower()
                    print(f"   Instaloader attempt {attempt + 1} failed: {e}")
                    if 'rate' in error_str or 'blocked' in error_str or 'login' in error_str:
                        if attempt < 1:
                            await asyncio.sleep(3)
                            continue
                    if self.yt_dlp_available:
                        break  # fall through to yt-dlp
        
        # Fallback to yt-dlp
        if self.yt_dlp_available:
            await report("Trying yt-dlp fallback...", 30)
            try:
                # Try with auth first
                result = await self._download_with_ytdlp(url, temp_dir, use_auth=True)
                if not result.get("success"):
                    result = await self._download_with_ytdlp(url, temp_dir, use_auth=False)
                
                if result.get("success"):
                    # Try to enrich with metadata from the page
                    metadata = await self._extract_metadata_from_url(url, shortcode)
                    result["metadata"] = metadata
                    result["caption"] = self._format_metadata_caption(metadata)
                    await report("Download complete", 100)
                    return result
            except Exception as e:
                print(f"   yt-dlp fallback failed: {e}")
        
        return {"success": False, "error": "Download failed. Content may be private, restricted, or requires login."}
    
    # ── yt-dlp helper ───────────────────────────────────────────────────────
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
                if COOKIE_FILE.exists():
                    ydl_opts['cookiefile'] = str(COOKIE_FILE)
                else:
                    if sys.platform == "darwin":
                        ydl_opts['cookiesfrombrowser'] = ('safari',)
                    elif sys.platform == "win32":
                        ydl_opts['cookiesfrombrowser'] = ('chrome',)
                    elif IG_USERNAME and IG_PASSWORD:
                        ydl_opts['username'] = IG_USERNAME
                        ydl_opts['password'] = IG_PASSWORD
            else:
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
    
    # ── Story: instaloader ──────────────────────────────────────────────────
    async def _download_story_instaloader(self, url: str, job_id: str, allow_relogin: bool = True) -> dict:
        temp_dir = TEMP_DIR / f"ig_{job_id}"
        temp_dir.mkdir(exist_ok=True)
        
        story_info = self.is_story_url(url)
        if not story_info:
            return {"success": False, "error": "Invalid story URL"}
        
        username, story_id = story_info
        
        try:
            print(f"📥 Downloading story: {username} ({story_id})")
            from instaloader import Profile, StoryItem
            
            self.L.dirname_pattern = str(temp_dir)
            
            try:
                story = StoryItem.from_mediaid(self.L.context, int(story_id))
                self.L.download_storyitem(story, target=username)
                print(f"✅ Downloaded specific story item {story_id}")
            except Exception as inner_e:
                inner_err = str(inner_e).lower()
                print(f"⚠️ Specific story fetch failed: {inner_e}")
                
                if 'please wait a few minutes' in inner_err or '429' in inner_err:
                    return {"success": False, "error": "Instagram rate limit. Please try again in a few minutes."}
                
                if any(x in inner_err for x in ['login', 'unauthorized', '401', '403', 'redirect']) and allow_relogin:
                    print("   Detected login error. Forcing fresh login and retrying...")
                    if self._force_fresh_login():
                        return await self._download_story_instaloader(url, job_id, allow_relogin=False)
                    else:
                        return {"success": False, "error": "Session expired. Fresh login failed."}
                
                print("   Trying all stories from user...")
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
            if 'please wait a few minutes' in err_str or '429' in err_str:
                return {"success": False, "error": "Instagram rate limit. Please try again in a few minutes."}
            if any(x in err_str for x in ['login', 'unauthorized', '401', '403', 'redirect']) and allow_relogin:
                print("   Detected login error. Forcing fresh login and retrying...")
                if self._force_fresh_login():
                    return await self._download_story_instaloader(url, job_id, allow_relogin=False)
                else:
                    return {"success": False, "error": "Session expired. Fresh login failed."}
        
        return {"success": False, "error": "Failed to download story"}
    
    # ── Story: gallery-dl ───────────────────────────────────────────────────
    async def _download_story_gallery_dl(self, url: str, temp_dir: Path) -> dict:
        try:
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            print(f"   gallery-dl: downloading story from {username}")
            
            cookies_file = temp_dir / "gallery_cookies.txt"
            if COOKIE_FILE.exists():
                shutil.copy(COOKIE_FILE, cookies_file)
            else:
                self._export_cookies()
                if COOKIE_FILE.exists():
                    shutil.copy(COOKIE_FILE, cookies_file)
            
            cmd = [
                "gallery-dl",
                "--destination", str(temp_dir),
                "--filename", "{media_id}.{extension}",
                "--no-mtime",
                "--option", "extractor.instagram.include=stories",
                "--option", "extractor.instagram.videos=true",
            ]
            
            if cookies_file.exists():
                cmd.extend(["--cookies", str(cookies_file)])
                print(f"   Using cookies: {cookies_file}")
            else:
                print("   No cookies available, trying without auth...")
            
            cmd.append(url)
            print(f"   Running gallery-dl...")
            
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
    
    # ── Story: GraphQL ──────────────────────────────────────────────────────
    async def _download_story_graphql(self, url: str, temp_dir: Path) -> dict:
        try:
            import requests
            
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            print(f"   GraphQL: fetching story {story_id} from {username}")
            
            session_cookies = {}
            if hasattr(self.L.context, '_session') and self.L.context._session:
                for cookie in self.L.context._session.cookies:
                    if "instagram" in (cookie.domain or ""):
                        session_cookies[cookie.name] = cookie.value
            
            if not session_cookies:
                return {"success": False, "error": "No Instagram session available"}
            
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
            
            user_url = "https://www.instagram.com/api/v1/users/web_profile_info/"
            user_params = {"username": username}
            resp = session.get(user_url, params=user_params, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"GraphQL user lookup failed: {resp.status_code}"}
            
            user_data = resp.json()
            user_id = user_data.get("data", {}).get("user", {}).get("id")
            if not user_id:
                return {"success": False, "error": "Could not resolve user ID"}
            
            graphql_url = "https://www.instagram.com/graphql/query"
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
            
            resp = session.get(graphql_url, params=params, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"GraphQL failed: {resp.status_code}"}
            
            data = resp.json()
            reels_media = data.get("data", {}).get("reels_media", [])
            if not reels_media:
                return {"success": False, "error": "No stories found for this user"}
            
            items = reels_media[0].get("items", [])
            if not items:
                return {"success": False, "error": "No active stories found"}
            
            target_items = []
            for item in items:
                item_id = str(item.get("id", "")).split("_")[0]
                if item_id == story_id:
                    target_items = [item]
                    break
            if not target_items:
                target_items = items
            
            downloaded_files = []
            for idx, item in enumerate(target_items):
                media_urls = []
                
                video_resources = item.get("video_resources", [])
                if video_resources:
                    best_video = max(video_resources, key=lambda x: x.get("config_width", 0))
                    video_url = best_video.get("src")
                    if video_url:
                        media_urls.append((video_url, ".mp4"))
                
                display_resources = item.get("display_resources", [])
                if display_resources and not video_resources:
                    best_image = max(display_resources, key=lambda x: x.get("config_width", 0))
                    image_url = best_image.get("src")
                    if image_url:
                        media_urls.append((image_url, ".jpg"))
                
                if not media_urls:
                    display_url = item.get("display_url")
                    if display_url:
                        is_video = item.get("is_video", False)
                        ext = ".mp4" if is_video else ".jpg"
                        media_urls.append((display_url, ext))
                
                for media_url, ext in media_urls:
                    try:
                        file_resp = session.get(media_url, headers={
                            "Referer": "https://www.instagram.com/",
                            "Accept": "*/*",
                        }, timeout=60)
                        if file_resp.status_code == 200:
                            file_path = temp_dir / f"graphql_{idx}{ext}"
                            with open(file_path, 'wb') as f:
                                f.write(file_resp.content)
                            downloaded_files.append(str(file_path))
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
    
    # ── Story: curl_cffi ────────────────────────────────────────────────────
    async def _download_story_curl_cffi(self, url: str, temp_dir: Path) -> dict:
        try:
            from curl_cffi import requests as curl_requests
            
            story_info = self.is_story_url(url)
            if not story_info:
                return {"success": False, "error": "Invalid story URL"}
            
            username, story_id = story_info
            print("   Trying curl_cffi browser impersonation...")
            
            session = curl_requests.Session(impersonate="chrome120")
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.instagram.com/",
            }
            
            session_cookies = self.L.context._session.cookies if hasattr(self.L.context, '_session') and self.L.context._session else None
            if session_cookies:
                for cookie in session_cookies:
                    if "instagram" in (cookie.domain or ""):
                        session.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path or "/")
            
            resp = session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"curl_cffi got status {resp.status_code}"}
            
            html_content = resp.text
            media_urls = []
            
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
            
            og_video_match = re.search(r'<meta[^>]+property=[\'"]og:video[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_content)
            if og_video_match:
                media_urls.append(og_video_match.group(1))
            
            og_image_match = re.search(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_content)
            if og_image_match:
                media_urls.append(og_image_match.group(1))
            
            cdn_urls = re.findall(r'https?://[^\s"\'<>]+\.cdninstagram\.com/[^\s"\'<>]+\.(?:mp4|jpg)', html_content)
            media_urls.extend(cdn_urls)
            
            media_urls = list(dict.fromkeys([u for u in media_urls if u]))
            
            if not media_urls:
                return {"success": False, "error": "curl_cffi: no media URLs found in page"}
            
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
    
    # ── Web API fallback ────────────────────────────────────────────────────
    async def _download_via_web_api(self, url: str, temp_dir: Path) -> dict:
        import requests
        
        headers_base = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        # SnapSave.app
        try:
            print("   Trying SnapSave API...")
            ss_home = requests.get("https://snapsave.app/", headers=headers_base, timeout=15)
            token_match = re.search(r'name="token" value="([^"]+)"', ss_home.text)
            token = token_match.group(1) if token_match else ""
            
            payload = {"url": url, "token": token}
            ss_headers = {
                **headers_base,
                "Origin": "https://snapsave.app",
                "Referer": "https://snapsave.app/",
            }
            resp = requests.post("https://snapsave.app/action.php", data=payload, headers=ss_headers, timeout=30)
            
            if resp.status_code == 200:
                content = resp.text
                media_urls = []
                found = re.findall(r'data-src="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', content)
                media_urls.extend(found)
                found2 = re.findall(r'href="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', content)
                media_urls.extend(found2)
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
        
        # Instagram embed
        try:
            print("   Trying Instagram embed API...")
            shortcode = self.extract_shortcode(url)
            if shortcode:
                embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
                resp = requests.get(embed_url, headers={**headers_base, "Accept": "text/html"}, timeout=15)
                if resp.status_code == 200:
                    page_html = resp.text
                    media_urls = []
                    img_match = re.search(r'<img[^>]+src="(https?://[^"]+\.cdninstagram\.com/[^"]+)"', page_html)
                    if img_match:
                        media_urls.append(img_match.group(1))
                    video_match = re.search(r'<video[^>]+src="(https?://[^"]+)"', page_html)
                    if video_match:
                        media_urls.append(video_match.group(1))
                    og_match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page_html)
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
        
        # ssstik.io
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
                page_html = resp.text
                media_urls = []
                found = re.findall(r'href="(https?://[^"]+\.(?:mp4|jpg|jpeg|png))"', page_html)
                media_urls.extend(found)
                found2 = re.findall(r'data-href="(https?://[^"]+)"', page_html)
                media_urls.extend(found2)
                media_urls = list(dict.fromkeys([u for u in media_urls if u]))
                
                if media_urls:
                    downloaded_files = []
                    for i, media_url in enumerate(media_urls[:3]):
                        try:
                            file_resp = requests.get(media_url, headers={**headers_base, "Referer": "https://ssstik.io/"}, timeout=60, stream=True)
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


class YouTubeDownloader:
    async def download(self, url: str, job_id: str, progress_callback=None) -> dict:
        async def report(msg, pct):
            if progress_callback:
                await progress_callback({"type": "progress", "message": msg, "percent": pct})
        
        await report("Starting YouTube download...", 10)
        temp_dir = TEMP_DIR / f"yt_{job_id}"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(temp_dir / "yt_%(id)s.%(ext)s"),
                'prefer_free_formats': True,
                'retries': 5,
                'socket_timeout': 30,
                'fragment_retries': 5,
                'quiet': True,
            }
            
            await report("Downloading video...", 40)
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            
            await report("Processing...", 90)
            files = list(temp_dir.iterdir())
            if files:
                video_file = max(files, key=lambda x: x.stat().st_size)
                await report("Complete", 100)
                return {
                    "success": True,
                    "files": [str(video_file)],
                    "title": info.get('title', ''),
                    "temp_dir": str(temp_dir)
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Download failed"}


class TwitterDownloader:
    async def download(self, url: str, job_id: str, progress_callback=None) -> dict:
        async def report(msg, pct):
            if progress_callback:
                await progress_callback({"type": "progress", "message": msg, "percent": pct})
        
        await report("Starting X/Twitter download...", 10)
        temp_dir = TEMP_DIR / f"tw_{job_id}"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                'format': 'best[filesize<50M]/best',
                'outtmpl': str(temp_dir / "tw_%(id)s.%(ext)s"),
                'retries': 5,
                'socket_timeout': 30,
                'fragment_retries': 5,
                'quiet': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/605.1.15',
                },
            }
            
            await report("Downloading...", 40)
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            
            await report("Processing...", 90)
            files = list(temp_dir.iterdir())
            if files:
                video_file = max(files, key=lambda x: x.stat().st_size)
                await report("Complete", 100)
                return {
                    "success": True,
                    "files": [str(video_file)],
                    "title": info.get('description', '')[:400],
                    "author": info.get('uploader', 'unknown'),
                    "temp_dir": str(temp_dir)
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Download failed"}


# ─── Detect platform from URL ──────────────────────────────────────────────
def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "instagram.com" in url_lower or "instagr.am" in url_lower:
        return "instagram"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "x.com" in url_lower or "twitter.com" in url_lower:
        return "twitter"
    return "unknown"

# ─── Initialize downloaders ──────────────────────────────────────────────
ig_downloader = None
yt_downloader = None
tw_downloader = None

def init_downloaders():
    global ig_downloader, yt_downloader, tw_downloader
    print("🔧 Initializing downloaders...")
    ig_downloader = GracefulInstagramDownloader()
    yt_downloader = YouTubeDownloader()
    tw_downloader = TwitterDownloader()
    print("✅ Downloaders initialized")

# ─── FastAPI App ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite setup
    await asyncio.to_thread(_init_db)
    await asyncio.to_thread(_recover_stale_jobs)
    global jobs
    jobs = await asyncio.to_thread(_load_jobs_from_db)
    print(f"💾 Loaded {len(jobs)} job(s) from SQLite")
    
    # Clean old temp files + DB records at startup
    await asyncio.to_thread(_cleanup_old_files)
    await asyncio.to_thread(_cleanup_old_db_jobs)
    
    # Run sync init in a thread so blocking Playwright/browser calls don't freeze the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_downloaders)
    
    # Start periodic cleanup + cookie reload tasks
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    cookie_reload_task = asyncio.create_task(_periodic_cookie_reload())
    
    yield
    
    cleanup_task.cancel()
    cookie_reload_task.cancel()
    for task in (cleanup_task, cookie_reload_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Social Downloader API", lifespan=lifespan)

# ─── WebSocket Manager ─────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"🔌 WebSocket connected. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"🔌 WebSocket disconnected. Total: {len(self.active_connections)}")
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_json(message)
        except Exception as e:
            print(f"⚠️ WS send error: {e}")
    
    async def broadcast(self, message: dict):
        disconnected = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.disconnect(conn)

manager = ConnectionManager()

# ─── API Endpoints ─────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "Social Downloader API", "version": "1.1"}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "instagram_auth": ig_downloader.auth_ok if ig_downloader else False,
        "instagram_auth_error": ig_downloader.auth_error if ig_downloader else None,
        "instagram_anonymous_mode": ig_downloader.anonymous_mode if ig_downloader else True,
    }

@app.post("/download", response_model=DownloadResponse)
async def queue_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Queue a download job. Returns job_id immediately."""
    
    platform = request.platform or detect_platform(request.url)
    if platform == "unknown":
        raise HTTPException(status_code=400, detail="Unsupported URL. Supported: Instagram, YouTube, X/Twitter")
    
    job_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    jobs[job_id] = {
        "job_id": job_id,
        "url": request.url,
        "platform": platform,
        "status": "pending",
        "progress": 0,
        "files": [],
        "error": None,
        "metadata": None,
        "created_at": now,
        "updated_at": now,
    }
    
    # Persist immediately so the job survives restarts
    await asyncio.to_thread(_save_job_to_db, jobs[job_id])
    
    # Start download in background
    background_tasks.add_task(process_download, job_id, request.url, platform)
    
    return DownloadResponse(
        job_id=job_id,
        status="queued",
        message=f"Download queued for {platform}"
    )

@app.get("/job/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    """Get status of a download job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        files=job["files"],
        error=job.get("error"),
        metadata=job.get("metadata"),
    )

@app.get("/files/{job_id}")
async def list_job_files(job_id: str):
    """List all files for a download job with their download URLs."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    file_list = []
    seen = set()

    platform = job.get("platform", "instagram")
    
    for f in job.get("files", []):
        p = Path(f)
        if p.exists() and p.name not in seen:
            seen.add(p.name)
            size = p.stat().st_size
            size_human = _human_size(size)
            file_list.append({
                "filename": p.name,
                "url": f"/download-file/{job_id}/{p.name}",
                "thumbnail_url": f"/thumbnail/{job_id}/{p.name}",
                "size": size,
                "size_human": size_human,
            })
    
    # Fallback: scan the job directory recursively if stored paths are stale
    if not file_list:
        top_dir = _job_top_dir(job_id, platform)
        if top_dir.exists():
            for p in top_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov'] and p.name not in seen and not p.name.endswith('.thumb.jpg'):
                    seen.add(p.name)
                    size = p.stat().st_size
                    size_human = _human_size(size)
                    file_list.append({
                        "filename": p.name,
                        "url": f"/download-file/{job_id}/{p.name}",
                        "thumbnail_url": f"/thumbnail/{job_id}/{p.name}",
                        "size": size,
                        "size_human": size_human,
                    })

    return {
        "job_id": job_id,
        "status": job["status"],
        "files": file_list,
        "count": len(file_list),
    }


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human readable string."""
    if size_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"

def _job_top_dir(job_id: str, platform: str) -> Path:
    """Return the top-level temp directory for a job."""
    prefix = {"instagram": "ig", "youtube": "yt", "twitter": "tw"}.get(platform, "ig")
    return TEMP_DIR / f"{prefix}_{job_id}"

def _flatten_job_files(job_id: str, platform: str, files: List[str]) -> List[str]:
    """Move all downloaded files into the top-level job dir so URLs work."""
    top_dir = _job_top_dir(job_id, platform)
    top_dir.mkdir(exist_ok=True)
    flattened: List[str] = []
    seen_names: Dict[str, int] = {}
    
    for src_path_str in files:
        src = Path(src_path_str)
        if not src.exists():
            continue
        
        name = src.name
        # Handle name collisions
        if name in seen_names:
            stem = src.stem
            ext = src.suffix
            counter = seen_names[name] + 1
            while True:
                new_name = f"{stem}_{counter}{ext}"
                if not (top_dir / new_name).exists():
                    name = new_name
                    seen_names[src.name] = counter
                    break
                counter += 1
        else:
            seen_names[name] = 0
        
        dest = top_dir / name
        if src.resolve() != dest.resolve():
            shutil.move(str(src), str(dest))
        flattened.append(str(dest))
    
    return flattened

def _find_file_recursive(job_id: str, platform: str, filename: str) -> Optional[Path]:
    """Search for a filename recursively inside the job's temp directory."""
    top_dir = _job_top_dir(job_id, platform)
    if not top_dir.exists():
        return None
    for f in top_dir.rglob("*"):
        if f.is_file() and f.name == filename:
            return f
    return None

def _thumbnail_path(file_path: Path) -> Path:
    """Return the expected thumbnail path for a media file."""
    return file_path.with_suffix(f"{file_path.suffix}.thumb.jpg")

def _generate_thumbnail(file_path: Path) -> Optional[Path]:
    """Generate a JPEG thumbnail for an image or video file using ffmpeg."""
    thumb_path = _thumbnail_path(file_path)
    if thumb_path.exists():
        return thumb_path
    
    ext = file_path.suffix.lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']:
        return None
    
    try:
        # ffmpeg: scale to max 320px width, good quality
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(file_path),
            "-vf", "scale=320:-1",
            "-q:v", "2",
            "-frames:v", "1",
            str(thumb_path),
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )
        if result.returncode == 0 and thumb_path.exists():
            return thumb_path
    except Exception as e:
        print(f"⚠️ Thumbnail generation failed for {file_path}: {e}")
    
    return None

@app.get("/download-file/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a specific file from a completed job."""
    from urllib.parse import unquote
    decoded_filename = unquote(filename)
    
    # Determine platform from job if we have it
    platform = jobs.get(job_id, {}).get("platform", "instagram")
    
    possible_paths = [
        TEMP_DIR / f"ig_{job_id}" / decoded_filename,
        TEMP_DIR / f"yt_{job_id}" / decoded_filename,
        TEMP_DIR / f"tw_{job_id}" / decoded_filename,
        TEMP_DIR / decoded_filename,
    ]
    
    file_path = None
    for p in possible_paths:
        if p.exists():
            file_path = p
            break
    
    # Fallback: recursive search inside the job directory (downloaders may create subfolders)
    if not file_path:
        file_path = _find_file_recursive(job_id, platform, decoded_filename)
    
    if not file_path:
        print(f"  [404] File not found: {decoded_filename}")
        for p in possible_paths:
            print(f"    - {p} (exists={p.exists()})")
        top_dir = _job_top_dir(job_id, platform)
        if top_dir.exists():
            print(f"  [404] Recursive contents of {top_dir}:")
            for f in top_dir.rglob("*"):
                if f.is_file():
                    print(f"    - {f.relative_to(top_dir)}")
        raise HTTPException(status_code=404, detail="File not found")
    
    safe_filename = decoded_filename.replace('"', "'")
    
    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type="application/octet-stream"
    )

@app.get("/thumbnail/{job_id}/{filename}")
async def thumbnail_file(job_id: str, filename: str):
    """Serve a thumbnail for a job file. Generates it on first request."""
    from urllib.parse import unquote
    decoded_filename = unquote(filename)
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    platform = jobs[job_id].get("platform", "instagram")
    top_dir = _job_top_dir(job_id, platform)
    file_path = top_dir / decoded_filename
    
    if not file_path.exists():
        file_path = _find_file_recursive(job_id, platform, decoded_filename)
    
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Generate thumbnail in a thread so ffmpeg doesn't block the event loop
    thumb_path = await asyncio.to_thread(_generate_thumbnail, file_path)
    
    if not thumb_path or not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail could not be generated")
    
    safe_filename = thumb_path.name.replace('"', "'")
    
    return FileResponse(
        path=str(thumb_path),
        filename=safe_filename,
        media_type="image/jpeg"
    )

# ─── WebSocket Endpoint ────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "subscribe":
                job_id = data.get("job_id")
                if job_id and job_id in jobs:
                    await manager.send_personal_message({
                        "type": "subscribed",
                        "job_id": job_id,
                        "status": jobs[job_id]["status"],
                    }, websocket)
                else:
                    await manager.send_personal_message({
                        "type": "error",
                        "message": "Job not found"
                    }, websocket)
            
            elif msg_type == "ping":
                await manager.send_personal_message({"type": "pong"}, websocket)
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"⚠️ WebSocket error: {e}")
        manager.disconnect(websocket)

# ─── Background Download Processor ─────────────────────────────────────────
async def process_download(job_id: str, url: str, platform: str):
    """Process download and broadcast progress via WebSocket."""
    
    async def progress_callback(data: dict):
        """Send progress update to all connected clients."""
        jobs[job_id]["progress"] = data.get("percent", 0)
        jobs[job_id]["status"] = "downloading"
        await manager.broadcast({
            "type": "progress",
            "job_id": job_id,
            **data
        })
    
    async def persist():
        """Persist current job state to SQLite (non-blocking)."""
        await asyncio.to_thread(_save_job_to_db, jobs[job_id])
    
    try:
        jobs[job_id]["status"] = "downloading"
        await persist()
        
        if platform == "instagram":
            result = await ig_downloader.download(url, job_id, progress_callback)
        elif platform == "youtube":
            result = await yt_downloader.download(url, job_id, progress_callback)
        elif platform == "twitter":
            result = await tw_downloader.download(url, job_id, progress_callback)
        else:
            result = {"success": False, "error": "Unsupported platform"}
        
        if result.get("success"):
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["progress"] = 100
            
            # Flatten files into the top-level job directory so /download-file URLs work
            raw_files = result.get("files", [])
            flattened_files = await asyncio.to_thread(_flatten_job_files, job_id, platform, raw_files)
            jobs[job_id]["files"] = flattened_files
            jobs[job_id]["metadata"] = result.get("metadata", {})
            await persist()
            
            # Build file URLs
            file_urls = []
            for f in flattened_files:
                fname = Path(f).name
                file_urls.append(f"/download-file/{job_id}/{fname}")
            
            # Broadcast completion
            await manager.broadcast({
                "type": "completed",
                "job_id": job_id,
                "files": file_urls,
                "metadata": result.get("metadata", {}),
                "method": result.get("method", "unknown"),
                "caption": result.get("caption", ""),
            })
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = result.get("error", "Unknown error")
            await persist()
            
            await manager.broadcast({
                "type": "failed",
                "job_id": job_id,
                "error": result.get("error", "Unknown error"),
            })
    
    except Exception as e:
        print(f"❌ Download processor error: {e}")
        traceback.print_exc()
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        await persist()
        
        await manager.broadcast({
            "type": "failed",
            "job_id": job_id,
            "error": str(e),
        })

# ─── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 Starting API Server on {SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="info")
