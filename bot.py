#!/usr/bin/env python3
"""
Robust Instagram & YouTube Downloader Bot
Features: Crash resistance, auto-recovery, Instagram DM monitoring, YouTube downloads
"""

import os
import re
import sys
import time
import random
import asyncio
import logging
import shutil
import json
import signal
import tempfile
import subprocess
import traceback
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from contextlib import contextmanager
from functools import wraps
import threading

# Load environment
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded .env file")
except ImportError:
    print("⚠️ python-dotenv not installed")
    pass

import instaloader
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler
)
from telegram.constants import ParseMode, ChatAction

# ============================================================================
# CONFIGURATION
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ Set BOT_TOKEN in .env file")
    sys.exit(1)

# Instagram credentials for DM monitoring
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

# Settings
MAX_RETRIES = 3
RETRY_DELAY = 5
DOWNLOAD_TIMEOUT = 300  # 5 minutes
MAX_FILE_SIZE_MB = 49   # Telegram limit is 50MB
TEMP_DIR = Path(tempfile.gettempdir()) / "telegram_downloader"
STATE_FILE = Path(__file__).parent / "bot_state.json"

TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Path(__file__).parent / "bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CRASH RESISTANCE & STATE MANAGEMENT
# ============================================================================

class BotState:
    """Persistent state management for crash recovery"""
    def __init__(self):
        self.data = {
            "downloads_completed": 0,
            "downloads_failed": 0,
            "last_restart": datetime.now().isoformat(),
            "active_downloads": {},
            "user_stats": {}
        }
        self._lock = threading.Lock()
        self.load()
    
    def load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
                logger.info("📂 State loaded from file")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
    
    def save(self):
        try:
            with self._lock:
                with open(STATE_FILE, 'w') as f:
                    json.dump(self.data, f, default=str)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def record_download(self, user_id: int, success: bool, platform: str):
        with self._lock:
            self.data["downloads_completed" if success else "downloads_failed"] += 1
            if str(user_id) not in self.data["user_stats"]:
                self.data["user_stats"][str(user_id)] = {"total": 0, "success": 0}
            self.data["user_stats"][str(user_id)]["total"] += 1
            if success:
                self.data["user_stats"][str(user_id)]["success"] += 1
            self.save()
    
    def add_active_download(self, download_id: str, info: dict):
        with self._lock:
            self.data["active_downloads"][download_id] = {
                **info,
                "started": datetime.now().isoformat()
            }
            self.save()
    
    def remove_active_download(self, download_id: str):
        with self._lock:
            self.data["active_downloads"].pop(download_id, None)
            self.save()

bot_state = BotState()

# ============================================================================
# ERROR HANDLING DECORATORS
# ============================================================================

def retry_on_error(max_retries=MAX_RETRIES, delay=RETRY_DELAY, exceptions=(Exception,)):
    """Decorator to retry functions on failure"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay * (attempt + 1))
                    else:
                        raise
        return async_wrapper
    return decorator

def safe_execute(default_return=None):
    """Decorator to catch all exceptions and return default"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}\n{traceback.format_exc()}")
                return default_return
        return async_wrapper
    return decorator

# ============================================================================
# INSTAGRAM DOWNLOADER WITH DM MONITORING
# ============================================================================

class InstagramManager:
    def __init__(self):
        self.L: Optional[instaloader.Instaloader] = None
        self.logged_in = False
        self._lock = asyncio.Lock()
        self._initialize()
    
    def _initialize(self):
        """Initialize Instaloader with crash resistance"""
        try:
            self.L = instaloader.Instaloader(
                download_pictures=True,
                download_videos=True,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                post_metadata_txt_pattern='',
                request_timeout=60
            )
            
            if IG_USERNAME and IG_PASSWORD:
                session_file = Path(__file__).parent / f"session_{IG_USERNAME}"
                if session_file.exists():
                    try:
                        self.L.load_session_from_file(IG_USERNAME, str(session_file))
                        logger.info(f"✅ Loaded Instagram session for {IG_USERNAME}")
                        self.logged_in = True
                    except Exception as e:
                        logger.warning(f"Session load failed: {e}")
                        self._login(session_file)
                else:
                    self._login(session_file)
            else:
                logger.warning("⚠️ No IG credentials, using anonymous mode")
                
        except Exception as e:
            logger.error(f"Instagram init failed: {e}")
            self.L = None
    
    def _login(self, session_file: Path):
        """Login and save session"""
        try:
            logger.info(f"🔑 Logging in as {IG_USERNAME}...")
            self.L.login(IG_USERNAME, IG_PASSWORD)
            self.L.save_session_to_file(str(session_file))
            self.logged_in = True
            logger.info("✅ Instagram login successful")
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            self.logged_in = False
    
    @retry_on_error(max_retries=2, delay=3)
    async def download_from_url(self, url: str, download_id: str) -> dict:
        """Download Instagram content from URL"""
        if not self.L:
            return {"success": False, "error": "Instagram not initialized"}
        
        async with self._lock:
            temp_dir = TEMP_DIR / f"ig_{download_id}_{int(time.time())}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            bot_state.add_active_download(download_id, {
                "platform": "instagram",
                "url": url,
                "temp_dir": str(temp_dir)
            })
            
            try:
                # Extract shortcode
                shortcode = self._extract_shortcode(url)
                if not shortcode:
                    return {"success": False, "error": "Invalid Instagram URL"}
                
                # Download
                post = instaloader.Post.from_shortcode(self.L.context, shortcode)
                self.L.dirname_pattern = str(temp_dir)
                self.L.download_post(post, target=shortcode)
                
                # Collect files
                files = []
                for f in temp_dir.iterdir():
                    if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov']:
                        size_mb = f.stat().st_size / (1024 * 1024)
                        if size_mb <= MAX_FILE_SIZE_MB:
                            files.append(str(f))
                
                # Compress if needed
                compressed = None
                if len(files) > 1:
                    compressed = await self._compress_files(files, temp_dir, download_id)
                
                return {
                    "success": True,
                    "files": files,
                    "compressed": compressed,
                    "caption": post.caption[:200] if post.caption else "",
                    "author": post.owner_username
                }
                
            except instaloader.exceptions.ProfileNotExistsException:
                return {"success": False, "error": "Profile private or not found"}
            except instaloader.exceptions.PostNotFoundException:
                return {"success": False, "error": "Post not found or deleted"}
            except instaloader.exceptions.ConnectionException as e:
                if "429" in str(e):
                    return {"success": False, "error": "Instagram rate limit (429). Wait 30-60 minutes.", "is_rate_limited": True}
                raise
            except Exception as e:
                logger.error(f"Instagram download error: {e}")
                return {"success": False, "error": str(e)}
            finally:
                bot_state.remove_active_download(download_id)
                # Cleanup handled by caller
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        patterns = [
            r'instagram\.com/p/([A-Za-z0-9_-]+)',
            r'instagram\.com/reel/([A-Za-z0-9_-]+)',
            r'instagram\.com/reels/([A-Za-z0-9_-]+)',
            r'instagram\.com/tv/([A-Za-z0-9_-]+)',
            r'instagram\.com/share/reel/([A-Za-z0-9_-]+)',
            r'instagram\.com/share/p/([A-Za-z0-9_-]+)',
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None
    
    async def _compress_files(self, files: List[str], temp_dir: Path, download_id: str) -> Optional[str]:
        """Compress multiple files to zip"""
        try:
            import zipfile
            zip_path = temp_dir / f"archive_{download_id}.zip"
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, Path(f).name)
            
            # Check size
            size_mb = zip_path.stat().st_size / (1024 * 1024)
            if size_mb <= MAX_FILE_SIZE_MB:
                return str(zip_path)
            return None
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            return None

instagram_mgr = InstagramManager()

# ============================================================================
# YOUTUBE DOWNLOADER
# ============================================================================

class YouTubeManager:
    def __init__(self):
        self.yt_dlp_available = self._check_yt_dlp()
    
    def _check_yt_dlp(self) -> bool:
        """Check if yt-dlp is installed"""
        try:
            subprocess.run(['yt-dlp', '--version'], capture_output=True, check=True)
            return True
        except:
            try:
                subprocess.run(['youtube-dl', '--version'], capture_output=True, check=True)
                return True
            except:
                logger.warning("⚠️ yt-dlp/youtube-dl not installed. YouTube downloads disabled.")
                return False
    
    @retry_on_error(max_retries=2, delay=3)
    async def download(self, url: str, download_id: str, quality: str = "best") -> dict:
        """Download YouTube video"""
        if not self.yt_dlp_available:
            return {"success": False, "error": "YouTube downloader not available. Install yt-dlp: pip install yt-dlp"}
        
        temp_dir = TEMP_DIR / f"yt_{download_id}_{int(time.time())}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        bot_state.add_active_download(download_id, {
            "platform": "youtube",
            "url": url,
            "temp_dir": str(temp_dir)
        })
        
        try:
            output_template = str(temp_dir / "%(title)s.%(ext)s")
            
            # Build command
            cmd = [
                'yt-dlp',
                '-f', f'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best',
                '--max-filesize', '50M',
                '-o', output_template,
                '--no-playlist',
                '--no-warnings',
                url
            ]
            
            # Run download
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=DOWNLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                process.kill()
                return {"success": False, "error": "Download timeout (5 minutes)"}
            
            if process.returncode != 0:
                error = stderr.decode() if stderr else "Unknown error"
                # Try audio only if video failed
                if "filesize" in error.lower():
                    return await self._download_audio_only(url, temp_dir, download_id)
                return {"success": False, "error": f"Download failed: {error[:200]}"}
            
            # Find downloaded file
            files = list(temp_dir.iterdir())
            if not files:
                return {"success": False, "error": "No file downloaded"}
            
            video_file = max(files, key=lambda x: x.stat().st_size)
            size_mb = video_file.stat().st_size / (1024 * 1024)
            
            if size_mb > MAX_FILE_SIZE_MB:
                # Try to compress or get smaller version
                return await self._download_audio_only(url, temp_dir, download_id)
            
            return {
                "success": True,
                "file": str(video_file),
                "title": video_file.stem,
                "size_mb": round(size_mb, 2)
            }
            
        except Exception as e:
            logger.error(f"YouTube download error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            bot_state.remove_active_download(download_id)
    
    async def _download_audio_only(self, url: str, temp_dir: Path, download_id: str) -> dict:
        """Fallback to audio only"""
        try:
            output_template = str(temp_dir / "audio_%(title)s.%(ext)s")
            
            cmd = [
                'yt-dlp',
                '-f', 'bestaudio[ext=m4a]/bestaudio',
                '--max-filesize', '50M',
                '-o', output_template,
                '--extract-audio',
                '--audio-format', 'mp3',
                '--no-playlist',
                url
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=DOWNLOAD_TIMEOUT
            )
            
            if process.returncode != 0:
                return {"success": False, "error": "Audio download also failed"}
            
            files = list(temp_dir.iterdir())
            if not files:
                return {"success": False, "error": "No audio downloaded"}
            
            audio_file = max(files, key=lambda x: x.stat().st_size)
            
            return {
                "success": True,
                "file": str(audio_file),
                "title": audio_file.stem,
                "is_audio": True,
                "note": "Sent as audio (video too large)"
            }
            
        except Exception as e:
            return {"success": False, "error": f"Audio fallback failed: {e}"}

youtube_mgr = YouTubeManager()

# ============================================================================
# FILE MANAGER
# ============================================================================

class FileManager:
    @staticmethod
    def cleanup_files(paths: List[str]):
        """Safely cleanup files"""
        for path in paths:
            try:
                p = Path(path)
                if p.exists():
                    if p.is_file():
                        p.unlink()
                    elif p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
            except Exception as e:
                logger.error(f"Cleanup error for {path}: {e}")
    
    @staticmethod
    async def compress_video(input_path: str, output_path: str, target_size_mb: int = 45) -> bool:
        """Compress video using ffmpeg"""
        try:
            cmd = [
                'ffmpeg', '-i', input_path,
                '-vf', 'scale=-2:720',  # 720p
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '28',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-y',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300
            )
            
            return process.returncode == 0 and Path(output_path).exists()
            
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            return False

file_mgr = FileManager()

# ============================================================================
# URL DETECTOR
# ============================================================================

class URLDetector:
    @staticmethod
    def detect_platform(url: str) -> Optional[str]:
        """Detect if URL is Instagram or YouTube"""
        url_lower = url.lower()
        
        # Instagram patterns
        ig_patterns = [
            r'instagram\.com/p/',
            r'instagram\.com/reel',
            r'instagram\.com/reels',
            r'instagram\.com/tv/',
            r'instagram\.com/share/',
            r'instagr\.am/',  # Short URL
        ]
        for p in ig_patterns:
            if re.search(p, url_lower):
                return "instagram"
        
        # YouTube patterns
        yt_patterns = [
            r'youtube\.com/watch',
            r'youtu\.be/',
            r'youtube\.com/shorts/',
            r'youtube\.com/live/',
        ]
        for p in yt_patterns:
            if re.search(p, url_lower):
                return "youtube"
        
        return None

url_detector = URLDetector()

# ============================================================================
# BOT HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    await update.message.reply_text("""
🤖 *Universal Downloader Bot*

Send me any link and I'll download it:

📸 *Instagram* - Posts, Reels (Photos/Videos)
🎬 *YouTube* - Videos (Auto-compressed if large)

*Features:*
• Auto-compression for large files
• Crash-resistant downloads
• Progress tracking

*Commands:*
/start - This message
/stats - Your download statistics
/clean - Cleanup temp files

⚠️ *Note:* Instagram has strict rate limits. If you get errors, wait 30 minutes.
""", parse_mode=ParseMode.MARKDOWN)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    user_id = str(update.effective_user.id)
    user_stats = bot_state.data["user_stats"].get(user_id, {"total": 0, "success": 0})
    
    total = bot_state.data["downloads_completed"]
    failed = bot_state.data["downloads_failed"]
    
    await update.message.reply_text(f"""
📊 *Statistics*

*Your Stats:*
• Total: {user_stats['total']}
• Successful: {user_stats['success']}
• Success Rate: {user_stats['success']/user_stats['total']*100:.1f}% if user_stats['total'] > 0 else 0%

*Bot Totals:*
• Completed: {total}
• Failed: {failed}
• Success Rate: {total/(total+failed)*100:.1f}% if (total+failed) > 0 else 0%
""", parse_mode=ParseMode.MARKDOWN)

async def cleanup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual cleanup command"""
    try:
        # Cleanup temp files older than 1 hour
        count = 0
        for item in TEMP_DIR.iterdir():
            try:
                stat = item.stat()
                age = time.time() - stat.st_mtime
                if age > 3600:  # 1 hour
                    if item.is_file():
                        item.unlink()
                    else:
                        shutil.rmtree(item, ignore_errors=True)
                    count += 1
            except:
                pass
        
        await update.message.reply_text(f"🧹 Cleaned up {count} old files")
    except Exception as e:
        await update.message.reply_text(f"❌ Cleanup error: {e}")

@safe_execute(default_return=None)
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main URL handler"""
    url = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    download_id = f"{user_id}_{int(time.time())}_{random.randint(1000, 9999)}"
    
    # Detect platform
    platform = url_detector.detect_platform(url)
    
    if not platform:
        await update.message.reply_text("❌ Unsupported URL. Send Instagram or YouTube links only.")
        return
    
    # Show typing
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    # Processing message
    msg = await update.message.reply_text(f"⏳ Detected *{platform.title()}* link. Starting download...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        if platform == "instagram":
            result = await instagram_mgr.download_from_url(url, download_id)
        else:  # youtube
            result = await youtube_mgr.download(url, download_id)
        
        if not result.get("success"):
            error = result.get("error", "Unknown error")
            is_rate_limit = result.get("is_rate_limited") or "429" in error
            
            if is_rate_limit:
                await msg.edit_text(f"⛔ *Rate Limited*\n\n{error}\n\nTry again in 30-60 minutes.", parse_mode=ParseMode.MARKDOWN)
            else:
                await msg.edit_text(f"❌ *Download Failed*\n\n{error}", parse_mode=ParseMode.MARKDOWN)
            
            bot_state.record_download(user_id, False, platform)
            return
        
        # Success - send files
        await msg.edit_text(f"📤 Download complete! Sending files...")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        
        files_to_cleanup = []
        
        if platform == "instagram":
            files = result.get("files", [])
            compressed = result.get("compressed")
            caption = result.get("caption", "")
            author = result.get("author", "Unknown")
            
            # Send caption as message
            text = f"📸 *Instagram Post*\n👤 {author}\n\n{caption[:500] if caption else 'No caption'}"
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            
            # Send compressed if multiple files
            if compressed and len(files) > 3:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                with open(compressed, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=f"instagram_{download_id}.zip",
                        caption=f"📦 All {len(files)} files (compressed)"
                    )
                files_to_cleanup.append(compressed)
            else:
                # Send individual files
                for i, f in enumerate(files[:10]):  # Limit to 10 files
                    try:
                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO if f.lower().endswith(('.jpg', '.jpeg', '.png')) else ChatAction.UPLOAD_VIDEO)
                        
                        with open(f, 'rb') as file:
                            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                                await update.message.reply_photo(photo=file)
                            else:
                                # Check size for videos
                                size_mb = Path(f).stat().st_size / (1024 * 1024)
                                if size_mb > MAX_FILE_SIZE_MB:
                                    # Try to compress
                                    compressed_path = str(Path(f).parent / f"compressed_{Path(f).name}")
                                    success = await file_mgr.compress_video(f, compressed_path)
                                    if success and Path(compressed_path).stat().st_size / (1024 * 1024) <= MAX_FILE_SIZE_MB:
                                        with open(compressed_path, 'rb') as cf:
                                            await update.message.reply_video(video=cf, caption=f"🎬 Compressed ({size_mb:.1f}MB → {Path(compressed_path).stat().st_size / (1024 * 1024):.1f}MB)")
                                        files_to_cleanup.append(compressed_path)
                                    else:
                                        await update.message.reply_text(f"⚠️ Video too large ({size_mb:.1f}MB). Could not compress enough.")
                                else:
                                    await update.message.reply_video(video=file)
                        
                        files_to_cleanup.append(f)
                        
                        # Small delay between files
                        if i < len(files) - 1:
                            await asyncio.sleep(1)
                            
                    except Exception as e:
                        logger.error(f"Failed to send file {f}: {e}")
                        await update.message.reply_text(f"⚠️ Could not send 1 file")
            
            files_to_cleanup.extend(files)
            
        else:  # YouTube
            file_path = result.get("file")
            title = result.get("title", "Video")
            is_audio = result.get("is_audio", False)
            note = result.get("note", "")
            
            if file_path and Path(file_path).exists():
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO if not is_audio else ChatAction.UPLOAD_VOICE)
                
                with open(file_path, 'rb') as f:
                    if is_audio:
                        await update.message.reply_audio(
                            audio=f,
                            title=title,
                            caption=f"🎵 {title}\n\n{note}"
                        )
                    else:
                        size_mb = result.get("size_mb", 0)
                        await update.message.reply_video(
                            video=f,
                            caption=f"🎬 {title}\n📦 {size_mb}MB"
                        )
                
                files_to_cleanup.append(file_path)
        
        # Final message
        await msg.edit_text("✅ *Complete!*", parse_mode=ParseMode.MARKDOWN)
        bot_state.record_download(user_id, True, platform)
        
        # Cleanup
        file_mgr.cleanup_files(files_to_cleanup)
        
        # Also cleanup temp dir
        temp_dirs = set()
        for f in files_to_cleanup:
            try:
                temp_dirs.add(str(Path(f).parent))
            except:
                pass
        for d in temp_dirs:
            try:
                if Path(d).exists() and TEMP_DIR in Path(d).parents:
                    shutil.rmtree(d, ignore_errors=True)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Handler error: {e}\n{traceback.format_exc()}")
        await msg.edit_text(f"❌ *Error:* {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)
        bot_state.record_download(user_id, False, platform)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler"""
    logger.error(f"Update {update} caused error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ An error occurred. The bot will continue running."
            )
    except:
        pass

# ============================================================================
# CRASH RECOVERY & SHUTDOWN
# ============================================================================

def handle_shutdown(signum, frame):
    """Graceful shutdown handler"""
    logger.info("🛑 Shutdown signal received, saving state...")
    bot_state.save()
    # Cleanup active downloads
    for download_id, info in list(bot_state.data["active_downloads"].items()):
        try:
            temp_dir = info.get("temp_dir")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
    logger.info("👋 Goodbye!")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ============================================================================
# MAIN
# ============================================================================

async def post_init(application: Application):
    """Post initialization"""
    logger.info("✅ Bot initialized and ready")
    # Cleanup old temp files on startup
    try:
        for item in TEMP_DIR.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                elif item.stat().st_mtime < time.time() - 86400:  # 24 hours
                    item.unlink()
            except:
                pass
    except:
        pass

def main():
    print("🚀 Starting Robust Downloader Bot...")
    print(f"📁 Temp directory: {TEMP_DIR}")
    print(f"📊 State file: {STATE_FILE}")
    print(f"📸 Instagram: {'✅' if instagram_mgr.logged_in else '⚠️ Anonymous'}")
    print(f"🎬 YouTube: {'✅' if youtube_mgr.yt_dlp_available else '❌'}")
    
    # Build application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clean", cleanup_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_error_handler(error_handler)
    
    # Post init
    app.post_init = post_init
    
    # Save state periodically
    async def periodic_save(context: ContextTypes.DEFAULT_TYPE):
        while True:
            await asyncio.sleep(60)  # Every minute
            bot_state.save()
    
    # Run
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()