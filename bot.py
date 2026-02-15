#!/usr/bin/env python3
"""
Fixed Instagram & YouTube Downloader Bot
"""

import os
import re
import sys
import time
import random
import asyncio
import logging
import shutil
import traceback
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded .env file")
except ImportError:
    print("⚠️ python-dotenv not installed")
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
from telegram.helpers import escape_markdown

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
# INSTAGRAM DOWNLOADER - FIXED EXCEPTIONS
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
            request_timeout=60,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._login()
    
    def _login(self):
        if not IG_USERNAME or not IG_PASSWORD:
            print("⚠️ No IG credentials - using anonymous (very low limits)")
            return
        
        try:
            session_file = Path(f"session_{IG_USERNAME}")
            if session_file.exists():
                # Try session first, but if it fails, re-login
                try:
                    self.L.load_session_from_file(IG_USERNAME, str(session_file))
                    print(f"✅ Loaded session for {IG_USERNAME}")
                    return
                except Exception as e:
                    print(f"⚠️ Session load failed ({e}), re-logging in...")
            
            # Fresh login
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
                return {"success": False, "error": "Could not find post code in URL"}
            
            print(f"📥 Downloading post: {shortcode}")
            
            # Get post
            post = instaloader.Post.from_shortcode(self.L.context, shortcode)
            
            # Download
            self.L.dirname_pattern = str(temp_dir)
            self.L.download_post(post, target=shortcode)
            
            # Collect files
            files = []
            for f in temp_dir.iterdir():
                if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    if size_mb <= 50:
                        files.append(str(f))
                        print(f"  ✓ Found: {f.name} ({size_mb:.1f}MB)")
            
            if not files:
                return {"success": False, "error": "No media files found"}
            
            return {
                "success": True,
                "files": files,
                "caption": post.caption[:300] if post.caption else "",
                "author": post.owner_username,
                "temp_dir": str(temp_dir)
            }
            
        # FIXED: Use correct exception names
        except instaloader.exceptions.LoginRequiredException:
            return {"success": False, "error": "Login required - post may be private"}
        except instaloader.exceptions.ProfileNotExistsException:
            return {"success": False, "error": "Profile not found or private"}
        except instaloader.exceptions.QueryReturnedNotFoundException:
            return {"success": False, "error": "Post not found (deleted or private)"}
        except instaloader.exceptions.BadResponseException as e:
            # Try fallback with yt-dlp
            print(f"⚠️ Instaloader failed, trying yt-dlp fallback...")
            return await self._download_with_ytdlp(url, download_id, temp_dir)
        except instaloader.exceptions.ConnectionException as e:
            if "429" in str(e):
                return {"success": False, "error": "⛔ INSTAGRAM RATE LIMIT (429)\n\nToo many requests. Wait 30-60 minutes."}
            return {"success": False, "error": f"Connection error: {e}"}
        except Exception as e:
            print(f"❌ Download error: {e}")
            traceback.print_exc()
            return {"success": False, "error": f"Error: {str(e)[:200]}"}
    
    async def _download_with_ytdlp(self, url: str, download_id: str, temp_dir: Path) -> dict:
        """Fallback download using yt-dlp when instaloader fails"""
        try:
            from yt_dlp import YoutubeDL
            
            output_path = str(temp_dir / "%(title)s.%(ext)s")
            
            ydl_opts = {
                'format': 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best',
                'outtmpl': output_path,
                'max_filesize': 50 * 1024 * 1024,
                'noplaylist': True,
                'cookiesfrombrowser': None,  # Don't use browser cookies
            }
            
            print(f"📥 Downloading with yt-dlp: {url}")
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'instagram_post')
                uploader = info.get('uploader', 'unknown')
            
            files = list(temp_dir.iterdir())
            if not files:
                return {"success": False, "error": "yt-dlp download failed - no file created"}
            
            # Get the largest file (usually the video)
            media_files = []
            for f in files:
                if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov', '.webp']:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    if size_mb <= 50:
                        media_files.append(str(f))
                        print(f"  ✓ Found: {f.name} ({size_mb:.1f}MB)")
            
            if not media_files:
                return {"success": False, "error": "No media files found after download"}
            
            return {
                "success": True,
                "files": media_files,
                "caption": title[:300] if title else "",
                "author": uploader if uploader else "unknown",
                "temp_dir": str(temp_dir)
            }
            
        except Exception as e:
            print(f"❌ yt-dlp fallback failed: {e}")
            error_str = str(e)
            if "inappropriate" in error_str.lower() or "unavailable" in error_str.lower():
                return {"success": False, "error": "This post is age-restricted or flagged as inappropriate by Instagram.\n\nI cannot download restricted content."}
            elif "login" in error_str.lower():
                return {"success": False, "error": "This post requires login to view.\n\nIt may be from a private account or age-restricted."}
            else:
                return {"success": False, "error": f"Download failed: {error_str[:100]}\n\nThe post may be deleted, private, or restricted."}

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
            return {"success": False, "error": "yt-dlp not installed. Run: pip install yt-dlp"}
        
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
            
            print(f"📥 Downloading YouTube: {url}")
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'video')
            
            files = list(temp_dir.iterdir())
            if not files:
                return {"success": False, "error": "Download failed - no file created"}
            
            video_file = max(files, key=lambda x: x.stat().st_size)
            size_mb = video_file.stat().st_size / (1024 * 1024)
            
            print(f"  ✓ Downloaded: {video_file.name} ({size_mb:.1f}MB)")
            
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
# BOT HANDLERS
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
    chat = update.effective_chat
    
    print(f"\n{'='*50}")
    print(f"📨 /start from User: {user.id} (@{user.username}), Chat: {chat.id}")
    print(f"{'='*50}\n")
    
    await update.message.reply_text(f"""
🤖 *Media Downloader Bot*

Hello {user.first_name}!

Send me links to download:
📸 *Instagram* - Posts, Reels
🎬 *YouTube* - Videos

Your Chat ID: `{chat.id}`
""", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    if not update.message or not update.message.text:
        return
    
    url = update.message.text.strip()
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"\n{'='*60}")
    print(f"📨 Message from User: {user.id}, Chat: {chat.id}")
    print(f"🔗 URL: {url[:80]}...")
    
    # Detect platform
    platform = detect_platform(url)
    if not platform:
        print(f"⏭️  Not a supported URL")
        return
    
    print(f"📱 Platform: {platform}")
    print(f"{'='*60}")
    
    download_id = f"{user.id}_{int(time.time())}"
    
    # Send processing message
    try:
        msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")
        print(f"✅ Processing message sent")
    except Exception as e:
        print(f"❌ Failed to send processing message: {e}")
        return
    
    try:
        # Download
        if platform == "instagram":
            result = await ig_downloader.download(url, download_id)
        else:
            result = await yt_downloader.download(url, download_id)
        
        # Check result
        if not result.get("success"):
            error = result.get("error", "Unknown error")
            print(f"❌ Download failed: {error}")
            await msg.edit_text(f"❌ *Error:*\n{error}", parse_mode=ParseMode.MARKDOWN)
            return
        
        # Success
        print(f"✅ Download successful, sending files...")
        await msg.edit_text("📤 Sending files...")
        
        if platform == "instagram":
            files = result.get("files", [])
            caption = result.get("caption", "")
            author = result.get("author", "unknown")
            
            # Send info - use HTML to avoid markdown parsing issues
            import html
            safe_caption = html.escape(caption[:400] if caption else '<i>No caption</i>')
            info_text = f"📸 <b>Instagram Post</b>\n👤 @{author}\n\n{safe_caption}"
            await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)
            
            # Send files
            sent_count = 0
            for i, f in enumerate(files[:10]):
                try:
                    print(f"📤 Sending file {i+1}/{len(files)}: {Path(f).name}")
                    
                    await context.bot.send_chat_action(chat.id, ChatAction.UPLOAD_PHOTO if f.lower().endswith(('.jpg', '.png')) else ChatAction.UPLOAD_VIDEO)
                    
                    with open(f, 'rb') as file:
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            await update.message.reply_photo(photo=file)
                        else:
                            await update.message.reply_video(video=file)
                    
                    sent_count += 1
                    print(f"✅ File {i+1} sent")
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    print(f"❌ Failed to send file: {e}")
            
            # Cleanup
            temp_dir = result.get("temp_dir")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            
            await msg.edit_text(f"✅ Sent {sent_count}/{len(files)} files")
            
        else:  # YouTube
            file_path = result.get("file")
            title = result.get("title")
            size = result.get("size_mb")
            
            with open(file_path, 'rb') as f:
                await update.message.reply_video(
                    video=f,
                    caption=f"🎬 {title}\n📦 {size}MB"
                )
            
            temp_dir = result.get("temp_dir")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            
            await msg.edit_text("✅ Done!")
        
        print(f"✅ Request completed\n")
        
    except Exception as e:
        print(f"❌ Handler error: {e}")
        traceback.print_exc()
        try:
            await msg.edit_text(f"❌ Error: {str(e)[:200]}")
        except:
            pass

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Global error: {context.error}")
    traceback.print_exc()

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "="*60)
    print("🚀 STARTING BOT")
    print("="*60)
    print(f"📸 Instagram: {'✅' if IG_USERNAME else '⚠️ Anonymous'}")
    print(f"🎬 YouTube: {'✅' if yt_downloader.available else '❌'}")
    print("="*60 + "\n")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    
    print("🤖 Bot running! Send /start in Telegram\n")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()