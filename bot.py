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
        temp_dir = TEMP_DIR / f"ig_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        shortcode = self.extract_shortcode(url)
        
        if not shortcode:
            return {"success": False, "error": "Invalid Instagram URL"}

        # Attempt Instaloader
        try:
            print(f"📥 Instaloader: {shortcode}")
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
            print(f"⚠️ Instaloader failed: {e}. Trying yt-dlp...")

        # Fallback to yt-dlp
        if self.yt_dlp_available:
            return await self._download_with_ytdlp(url, temp_dir)
        
        return {"success": False, "error": "Both download methods failed."}

    async def _download_with_ytdlp(self, url: str, temp_dir: Path) -> dict:
        try:
            from yt_dlp import YoutubeDL
            output_path = str(temp_dir / "inst_%(title)s.%(ext)s")
            
            ydl_opts = {
                'format': 'best[filesize<50M]/best',
                'outtmpl': output_path,
                'noplaylist': True,
                'quiet': True,
            }
            
            # Use cookies if available
            if COOKIE_FILE.exists():
                ydl_opts['cookiefile'] = str(COOKIE_FILE)
                print("🍪 Using cookies.txt for yt-dlp")

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
            return {"success": False, "error": f"yt-dlp failed: {str(e)[:100]}"}
        return {"success": False, "error": "No files found."}

    def _collect_files(self, temp_dir: Path) -> list:
        return [str(f) for f in temp_dir.iterdir() if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']]

    def extract_shortcode(self, url: str) -> str:
        m = re.search(r'instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
        return m.group(1) if m else None

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
                'format': 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best',
                'outtmpl': str(temp_dir / "%(title)s.%(ext)s"),
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
                with open(res["file"], 'rb') as f:
                    await update.message.reply_video(video=f, caption=res['title'])
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