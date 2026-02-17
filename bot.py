#!/usr/bin/env python3
"""
Instagram & YouTube Downloader Bot - Production Version
Handles age-restricted content gracefully
"""

import os
import re
import sys
import time
import random
import asyncio
import logging
import shutil
import html
import traceback
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
from telegram.constants import ParseMode, ChatAction

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

if not BOT_TOKEN:
    print("❌ ERROR: Set BOT_TOKEN in .env file")
    sys.exit(1)

print(f"🔑 Token loaded: {BOT_TOKEN[:20]}...")

# Setup
TEMP_DIR = Path("/tmp/telegram_downloader")
TEMP_DIR.mkdir(exist_ok=True)

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
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            request_timeout=60
        )
        self._login()
    
    def _login(self):
        if not IG_USERNAME or not IG_PASSWORD:
            print("⚠️ No IG credentials - anonymous mode (very limited)")
            return
        
        try:
            session_file = Path(f"session_{IG_USERNAME}")
            if session_file.exists():
                try:
                    self.L.load_session_from_file(IG_USERNAME, str(session_file))
                    print(f"✅ Loaded session for {IG_USERNAME}")
                    return
                except Exception as e:
                    print(f"⚠️ Session failed: {e}")
            
            print(f"🔑 Logging in as {IG_USERNAME}...")
            self.L.login(IG_USERNAME, IG_PASSWORD)
            self.L.save_session_to_file(str(session_file))
            print("✅ Login successful")
        except Exception as e:
            print(f"❌ Login failed: {e}")
    
    def extract_shortcode(self, url: str) -> str:
        patterns = [
            r'instagram\.com/p/([A-Za-z0-9_-]+)',
            r'instagram\.com/reel/([A-Za-z0-9_-]+)',
            r'instagram\.com/reels/([A-Za-z0-9_-]+)',
            r'instagram\.com/tv/([A-Za-z0-9_-]+)',
            r'instagr\.am/p/([A-Za-z0-9_-]+)',
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None
    
    async def download(self, url: str, download_id: str) -> dict:
        temp_dir = TEMP_DIR / f"ig_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            shortcode = self.extract_shortcode(url)
            if not shortcode:
                return {"success": False, "error": "Invalid Instagram URL"}
            
            print(f"📥 Downloading: {shortcode}")
            
            post = instaloader.Post.from_shortcode(self.L.context, shortcode)
            
            self.L.dirname_pattern = str(temp_dir)
            self.L.download_post(post, target=shortcode)
            
            files = []
            for f in temp_dir.iterdir():
                if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    if size_mb <= 50:
                        files.append(str(f))
                        print(f"  ✓ {f.name} ({size_mb:.1f}MB)")
            
            if not files:
                return {"success": False, "error": "No media files found"}
            
            return {
                "success": True,
                "files": files,
                "caption": post.caption[:400] if post.caption else "",
                "author": post.owner_username,
                "temp_dir": str(temp_dir)
            }
            
        except instaloader.exceptions.LoginRequiredException:
            return {"success": False, "error": "🔒 Private post - cannot access"}
        except instaloader.exceptions.ProfileNotExistsException:
            return {"success": False, "error": "Profile not found or private"}
        except instaloader.exceptions.QueryReturnedNotFoundException:
            return {"success": False, "error": "Post not found (deleted)"}
        except instaloader.exceptions.BadResponseException as e:
            error_str = str(e)
            if any(x in error_str.lower() for x in ['age', 'restricted', 'inappropriate', 'sensitive']):
                return {
                    "success": False,
                    "error": "🔞 Age-Restricted Content\n\nThis post is flagged by Instagram and cannot be downloaded.\n\nReasons:\n• Age-restricted (18+)\n• Sensitive/inappropriate content\n• Community guidelines violation\n\nYou must view this directly in the Instagram app."
                }
            return {"success": False, "error": f"Instagram error: {error_str[:100]}"}
        except instaloader.exceptions.ConnectionException as e:
            if "429" in str(e):
                return {"success": False, "error": "⛔ Rate limited. Wait 30-60 minutes."}
            if "401" in str(e):
                return {"success": False, "error": "❌ Session expired. Restart bot."}
            return {"success": False, "error": f"Connection error: {e}"}
        except Exception as e:
            error_str = str(e)
            if any(x in error_str.lower() for x in ['age', 'restricted', 'inappropriate']):
                return {
                    "success": False,
                    "error": "🔞 Age-Restricted or Inappropriate Content\n\nThis post cannot be downloaded due to Instagram's content policies."
                }
            print(f"❌ Error: {e}")
            return {"success": False, "error": f"Download failed: {error_str[:150]}"}

ig_downloader = InstagramDownloader()

# ============================================================================
# YOUTUBE DOWNLOADER
# ============================================================================

class YouTubeDownloader:
    def __init__(self):
        self.available = self._check()
    
    def _check(self) -> bool:
        try:
            import yt_dlp
            return True
        except ImportError:
            return False
    
    async def download(self, url: str, download_id: str) -> dict:
        if not self.available:
            return {"success": False, "error": "Run: pip install yt-dlp"}
        
        temp_dir = TEMP_DIR / f"yt_{download_id}"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            from yt_dlp import YoutubeDL
            
            output_path = str(temp_dir / "%(title)s.%(ext)s")
            
            ydl_opts = {
                'format': 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best',
                'outtmpl': output_path,
                'max_filesize': 50 * 1024 * 1024,
                'noplaylist': True,
            }
            
            print(f"📥 YouTube: {url}")
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'video')
            
            files = list(temp_dir.iterdir())
            if not files:
                return {"success": False, "error": "No file created"}
            
            video_file = max(files, key=lambda x: x.stat().st_size)
            size_mb = video_file.stat().st_size / (1024 * 1024)
            
            print(f"  ✓ {video_file.name} ({size_mb:.1f}MB)")
            
            return {
                "success": True,
                "file": str(video_file),
                "title": title,
                "size_mb": round(size_mb, 2),
                "temp_dir": str(temp_dir)
            }
            
        except Exception as e:
            print(f"❌ YouTube error: {e}")
            return {"success": False, "error": str(e)}

yt_downloader = YouTubeDownloader()

# ============================================================================
# BOT
# ============================================================================

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if any(x in url_lower for x in ['instagram.com', 'instagr.am']):
        return "instagram"
    if any(x in url_lower for x in ['youtube.com', 'youtu.be']):
        return "youtube"
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    await update.message.reply_text(f"""
🤖 *Media Downloader*

Hello {user.first_name}!

📸 *Instagram* - Posts, Reels, Stories
🎬 *YouTube* - Videos

⚠️ *Note:* Age-restricted Instagram content cannot be downloaded.

Send me a link to start!
""", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    url = update.message.text.strip()
    user = update.effective_user
    
    platform = detect_platform(url)
    if not platform:
        return
    
    download_id = f"{user.id}_{int(time.time())}"
    
    msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")
    
    try:
        if platform == "instagram":
            result = await ig_downloader.download(url, download_id)
        else:
            result = await yt_downloader.download(url, download_id)
        
        if not result.get("success"):
            await msg.edit_text(result.get("error", "Failed"), parse_mode=ParseMode.MARKDOWN)
            return
        
        await msg.edit_text("📤 Sending...")
        
        if platform == "instagram":
            files = result.get("files", [])
            caption = html.escape(result.get('caption', '')[:400])
            author = html.escape(result.get('author', 'unknown'))
            
            await update.message.reply_text(
                f"📸 <b>Instagram Post</b>\n👤 @{author}\n\n{caption if caption else '<i>No caption</i>'}",
                parse_mode=ParseMode.HTML
            )
            
            sent = 0
            for f in files[:10]:
                try:
                    with open(f, 'rb') as file:
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            await update.message.reply_photo(photo=file)
                        else:
                            await update.message.reply_video(video=file)
                    sent += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Send error: {e}")
            
            shutil.rmtree(result.get("temp_dir"), ignore_errors=True)
            await msg.edit_text(f"✅ {sent} files sent")
            
        else:  # YouTube
            with open(result.get("file"), 'rb') as f:
                await update.message.reply_video(
                    video=f,
                    caption=f"🎬 {result.get('title')}\n📦 {result.get('size_mb')}MB"
                )
            shutil.rmtree(result.get("temp_dir"), ignore_errors=True)
            await msg.edit_text("✅ Done!")
            
    except Exception as e:
        print(f"Error: {e}")
        await msg.edit_text(f"❌ Error: {str(e)[:200]}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error: {context.error}")

def main():
    print("\n" + "="*60)
    print("🚀 BOT STARTING")
    print("="*60)
    print(f"📸 Instagram: {'✅' if IG_USERNAME else '⚠️ Anonymous'}")
    print(f"🎬 YouTube: {'✅' if yt_downloader.available else '❌'}")
    print("="*60 + "\n")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    
    print("🤖 Running!\n")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()