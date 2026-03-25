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
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded .env file")
except ImportError:
    pass

import instaloader
from telegram import Update
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
        
        # 1. Try to load session file (Copied from Mac)
        if session_file.exists():
            try:
                self.L.load_session_from_file(IG_USERNAME, str(session_file))
                print(f"✅ Loaded Instagram session from file: {session_file.name}")
                return
            except Exception as e:
                print(f"⚠️ Session file failed: {e}")

        # 2. Try regular login
        if IG_USERNAME and IG_PASSWORD:
            try:
                print(f"🔑 Attempting login for {IG_USERNAME}...")
                self.L.login(IG_USERNAME, IG_PASSWORD)
                self.L.save_session_to_file(str(session_file))
                print("✅ Login successful and session saved")
            except Exception as e:
                print(f"❌ Password login failed: {e}")
                print("💡 Tip: Copy the session file from your Mac to this folder.")

    async def download(self, url: str, download_id: str) -> dict:
        if self.is_story_url(url):
            if self.yt_dlp_available:
                temp_dir = TEMP_DIR / f"ig_{download_id}"
                temp_dir.mkdir(exist_ok=True)
                return await self._download_with_ytdlp(url, temp_dir)
            return await self.download_story(url, download_id)
        
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
                'extractor_retries': 3,
                'fragment_retries': 3,
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
            
            if IG_USERNAME and IG_PASSWORD:
                ydl_opts['username'] = IG_USERNAME
                ydl_opts['password'] = IG_PASSWORD
            
            if COOKIE_FILE.exists():
                ydl_opts['cookiefile'] = str(COOKIE_FILE)

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

    async def download_story(self, url: str, download_id: str) -> dict:
        temp_dir = TEMP_DIR / f"ig_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        
        story_info = self.is_story_url(url)
        if not story_info:
            return {"success": False, "error": "Invalid story URL"}
        
        username, story_id = story_info
        
        try:
            print(f"📥 Downloading story: {username} ({story_id})")
            from instaloader import Profile
            
            profile = Profile.from_username(self.L.context, username)
            
            self.L.dirname_pattern = str(temp_dir)
            self.L.download_stories(userids=[profile.userid], target=username)
            
            files = self._collect_files(temp_dir)
            if files:
                return {
                    "success": True, "files": files, "method": "instaloader",
                    "caption": "", "author": username, "temp_dir": str(temp_dir)
                }
        except Exception as e:
            print(f"⚠️ Story download failed: {e}")
            if self.yt_dlp_available:
                return await self._download_with_ytdlp(url, temp_dir)
        
        return {"success": False, "error": "Failed to download story"}

ig_downloader = InstagramDownloader()

# ============================================================================
# YOUTUBE DOWNLOADER
# ============================================================================

class YouTubeDownloader:
    def __init__(self):
        self.server_ip = "100.97.53.84"
        self.server_port = 8080
    
    async def download(self, url: str, download_id: str) -> dict:
        temp_dir = TEMP_DIR / f"yt_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
                'outtmpl': str(temp_dir / "%(title)s.%(ext)s"),
                'prefer_free_formats': True,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            
            files = list(temp_dir.iterdir())
            if files:
                video_file = max(files, key=lambda x: x.stat().st_size)
                safe_name = f"{download_id}.mp4"
                dest_file = TEMP_DIR / safe_name
                shutil.move(str(video_file), str(dest_file))
                shutil.rmtree(temp_dir, ignore_errors=True)
                download_url = f"http://{self.server_ip}:{self.server_port}/{safe_name}"
                return {
                    "success": True, 
                    "file": str(dest_file), 
                    "title": info.get('title'), 
                    "temp_dir": str(TEMP_DIR),
                    "download_url": download_url,
                    "filename": safe_name
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Download failed"}

yt_downloader = YouTubeDownloader()

# ============================================================================
# FILE SERVER
# ============================================================================

class FileServer:
    def __init__(self, download_dir, ip="0.0.0.0", port=8080):
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        
        self.download_dir = download_dir
        self.ip = ip
        self.port = port
        
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(download_dir), **kwargs)
        
        self.handler = Handler
        
    def start(self):
        import threading
        from http.server import HTTPServer
        
        server = HTTPServer((self.ip, self.port), self.handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"📁 File server running on http://{self.ip}:{self.port}")

def daily_cleanup():
    import threading
    import time
    
    def cleanup_loop():
        while True:
            now = datetime.now()
            target = now.replace(hour=23, minute=59, second=0, microsecond=0)
            if now > target:
                target = target.replace(day=target.day + 1)
            wait_seconds = (target - now).total_seconds()
            time.sleep(wait_seconds)
            
            for item in TEMP_DIR.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception as e:
                    print(f"⚠️ Cleanup error: {e}")
            print("🧹 Daily cleanup completed")
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()

file_server = FileServer(TEMP_DIR)

# ============================================================================
# BOT HANDLERS
# ============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "instagram.com" in url or "instagr.am" in url:
        platform = "instagram"
    elif "youtube.com" in url or "youtu.be" in url:
        platform = "youtube"
    else: return

    msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")
    download_id = f"{update.effective_user.id}_{int(time.time())}"

    try:
        if platform == "instagram":
            res = await ig_downloader.download(url, download_id)
            if res["success"]:
                await msg.edit_text("📤 Sending...")
                for f in res["files"]:
                    with open(f, 'rb') as file:
                        if f.lower().endswith(('.mp4', '.mov')):
                            await update.message.reply_video(video=file, caption=f"👤 @{res['author']}")
                        else:
                            await update.message.reply_photo(photo=file)
                shutil.rmtree(res["temp_dir"], ignore_errors=True)
                await msg.delete()
            else:
                await msg.edit_text(f"❌ {res['error']}")
        else:
            res = await yt_downloader.download(url, download_id)
            if res["success"]:
                await msg.edit_text(f"✅ Downloaded: {res['title']}\n\n📥 Download: {res['download_url']}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

def main():
    file_server.start()
    daily_cleanup()
    print(f"🚀 Bot Starting | Token: {BOT_TOKEN[:10]}...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()