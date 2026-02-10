import os
import re
import asyncio
import logging
import subprocess
import ffmpeg
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
import yt_dlp
import instaloader
from pathlib import Path
import aiohttp

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('tg_bot_token')
IG_USERNAME = os.getenv('ig_username')
IG_PASSWORD = os.getenv('ig_pass')

DOWNLOAD_DIR = Path('downloads')
DOWNLOAD_DIR.mkdir(exist_ok=True)

ig_loader = None

SESSION_FILE = Path('session-' + (IG_USERNAME or 'default'))

def init_instagram():
    global ig_loader
    if IG_USERNAME and IG_PASSWORD:
        ig_loader = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern=""
        )
        try:
            # Try to load existing session first
            if SESSION_FILE.exists():
                logger.info(f"Loading existing Instagram session for {IG_USERNAME}")
                ig_loader.load_session_from_file(IG_USERNAME, str(SESSION_FILE))
                logger.info("Instagram session loaded successfully")
                return True
            
            # If no session file, try to login
            logger.info(f"Attempting Instagram login for {IG_USERNAME}")
            ig_loader.login(IG_USERNAME, IG_PASSWORD)
            ig_loader.save_session_to_file(str(SESSION_FILE))
            logger.info("Instagram login successful, session saved")
            return True
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            logger.error("Instagram 2FA required. Please disable 2FA or login manually.")
            return False
        except instaloader.exceptions.BadCredentialsException:
            logger.error("Instagram: Invalid username or password")
            return False
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            return False
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """👋 Welcome to Media Downloader Bot!

I can download:
📺 YouTube videos
📸 Instagram posts, reels, and stories

Just send me a link and I'll download it for you!

Commands:
/start - Show this message
/status - Check Instagram login status"""
    await update.message.reply_text(welcome_text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ig_loader and ig_loader.context.is_logged_in:
        await update.message.reply_text("✅ Instagram: Logged in")
    else:
        await update.message.reply_text("❌ Instagram: Not logged in\nAdd ig_username and ig_pass to .env file")

def compress_video(input_path: str, output_path: str, target_size_mb: int = 45):
    """Compress video to target size using ffmpeg"""
    try:
        probe = ffmpeg.probe(input_path)
        duration = float(probe['format']['duration'])
        target_bitrate = (target_size_mb * 8192) / duration
        
        (
            ffmpeg
            .input(input_path)
            .output(output_path, 
                    vcodec='libx264',
                    video_bitrate=f'{int(target_bitrate)}k',
                    acodec='aac',
                    audio_bitrate='128k',
                    preset='fast',
                    movflags='faststart')
            .overwrite_output()
            .run(quiet=True)
        )
        return True
    except Exception as e:
        logger.error(f"Compression error: {e}")
        return False

async def download_youtube(url: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        output_path = DOWNLOAD_DIR / f"yt_{chat_id}_%(title)s.%(ext)s"
        compressed_path = DOWNLOAD_DIR / f"yt_{chat_id}_compressed.mp4"
        
        ydl_opts = {
            'format': 'best[height<=720]/bestvideo[height<=720]+bestaudio/best',
            'outtmpl': str(output_path),
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'player_skip': ['webpage', 'config', 'js'],
                }
            },
            'cookiesfrombrowser': None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            title = info.get('title', 'Video')
            
            if not os.path.exists(filename):
                await context.bot.send_message(chat_id, "❌ Download failed")
                return
            
            file_size = os.path.getsize(filename)
            logger.info(f"Downloaded: {title} - Size: {file_size / (1024*1024):.1f}MB")
            
            try:
                if file_size > 50 * 1024 * 1024:
                    # Compress if over 50MB
                    await context.bot.send_message(chat_id, f"📦 Video is {file_size / (1024*1024):.1f}MB. Compressing...")
                    
                    if compress_video(filename, str(compressed_path)):
                        with open(compressed_path, 'rb') as video_file:
                            await context.bot.send_video(
                                chat_id, 
                                video=video_file, 
                                caption=f"📺 {title[:100]}",
                                read_timeout=300,
                                write_timeout=300
                            )
                        os.remove(compressed_path)
                    else:
                        await context.bot.send_message(chat_id, "❌ Compression failed")
                else:
                    # Send directly if under 50MB
                    with open(filename, 'rb') as video_file:
                        await context.bot.send_video(
                            chat_id, 
                            video=video_file, 
                            caption=f"📺 {title[:100]}",
                            read_timeout=300,
                            write_timeout=300
                        )
            except Exception as send_error:
                logger.error(f"Error sending video: {send_error}")
                await context.bot.send_message(chat_id, f"❌ Failed to send: {str(send_error)}")
            finally:
                if os.path.exists(filename):
                    os.remove(filename)
            
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        await context.bot.send_message(chat_id, f"❌ Error: {str(e)}")

async def download_instagram_with_session(url: str, shortcode: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Try downloading with authenticated session"""
    try:
        post = instaloader.Post.from_shortcode(ig_loader.context, shortcode)
        
        if post.is_video:
            video_url = post.video_url
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(video_url) as response:
                    if response.status == 200:
                        video_data = await response.read()
                        temp_file = DOWNLOAD_DIR / f"ig_{shortcode}.mp4"
                        with open(temp_file, 'wb') as f:
                            f.write(video_data)
                        
                        await context.bot.send_video(chat_id, video=open(temp_file, 'rb'), caption=post.caption[:200] if post.caption else "")
                        os.remove(temp_file)
                        return True
        else:
            for i, node in enumerate(post.get_sidecar_nodes() if post.mediacount > 1 else [post]):
                if node.is_video:
                    media_url = node.video_url
                else:
                    media_url = node.display_url
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(media_url, headers=headers) as response:
                        if response.status == 200:
                            media_data = await response.read()
                            ext = 'mp4' if node.is_video else 'jpg'
                            temp_file = DOWNLOAD_DIR / f"ig_{shortcode}_{i}.{ext}"
                            with open(temp_file, 'wb') as f:
                                f.write(media_data)
                            
                            if node.is_video:
                                await context.bot.send_video(chat_id, video=open(temp_file, 'rb'))
                            else:
                                await context.bot.send_photo(chat_id, photo=open(temp_file, 'rb'))
                            os.remove(temp_file)
            return True
        
        await context.bot.send_message(chat_id, "✅ Download complete!")
        return True
        
    except Exception as e:
        logger.error(f"Instagram session download failed: {e}")
        return False

async def download_instagram_fallback(url: str, shortcode: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fallback: Use yt-dlp for Instagram (works for public posts without auth)"""
    try:
        await context.bot.send_message(chat_id, "⏳ Trying alternative download method...")
        
        output_path = DOWNLOAD_DIR / f"ig_{chat_id}_%(title)s.%(ext)s"
        
        ydl_opts = {
            'format': 'best',
            'outtmpl': str(output_path),
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if 'entries' in info:
                info = info['entries'][0]
            filename = ydl.prepare_filename(info)
            title = info.get('title', 'Instagram Post')
            
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                
                if file_size > 50 * 1024 * 1024:
                    await context.bot.send_message(chat_id, f"📦 File is {file_size / (1024*1024):.1f}MB. Compressing...")
                    compressed_path = DOWNLOAD_DIR / f"ig_{chat_id}_compressed.mp4"
                    
                    if compress_video(filename, str(compressed_path)):
                        with open(compressed_path, 'rb') as f:
                            await context.bot.send_video(chat_id, video=f, caption=f"📸 {title[:100]}...")
                        os.remove(compressed_path)
                    else:
                        await context.bot.send_document(chat_id, document=open(filename, 'rb'), caption=f"📸 {title[:100]}")
                else:
                    with open(filename, 'rb') as f:
                        await context.bot.send_video(chat_id, video=f, caption=f"📸 {title[:100]}")
                
                os.remove(filename)
                return True
                
    except Exception as e:
        logger.error(f"Instagram fallback download error: {e}")
        return False
    
    return False

async def download_instagram(url: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    # Extract shortcode from URL
    shortcode = None
    patterns = [
        r'/p/([A-Za-z0-9_-]+)',
        r'/reel/([A-Za-z0-9_-]+)',
        r'/reels/([A-Za-z0-9_-]+)',
        r'/tv/([A-Za-z0-9_-]+)',
        r'/stories/[^/]+/([0-9]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            shortcode = match.group(1)
            break
    
    if not shortcode:
        await context.bot.send_message(chat_id, "❌ Invalid Instagram URL")
        return
    
    await context.bot.send_message(chat_id, "⏳ Downloading from Instagram...")
    
    # Try with authenticated session first (if available)
    if ig_loader and ig_loader.context.is_logged_in:
        success = await download_instagram_with_session(url, shortcode, chat_id, context)
        if success:
            return
    
    # Fallback to yt-dlp (works for public posts)
    success = await download_instagram_fallback(url, shortcode, chat_id, context)
    if success:
        return
    
    # Both methods failed
    await context.bot.send_message(
        chat_id, 
        "❌ Failed to download Instagram content.\n\n"
        "Possible reasons:\n"
        "• Post is private and requires login\n"
        "• Instagram is blocking automated requests\n"
        "• The post/reel doesn't exist\n\n"
        "For private posts, try:\n"
        "1. Make sure ig_username and ig_pass in .env are correct\n"
        "2. Run: python create_session.py\n"
        "3. Restart the bot"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    
    youtube_patterns = [
        r'^(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+$',
        r'^(https?://)?(www\.)?youtu\.be/.+$'
    ]
    
    instagram_patterns = [
        r'^(https?://)?(www\.)?instagram\.com/(p|reel|reels|tv|stories)/.+$'
    ]
    
    is_youtube = any(re.match(pattern, url) for pattern in youtube_patterns)
    is_instagram = any(re.match(pattern, url) for pattern in instagram_patterns)
    
    if is_youtube:
        await update.message.reply_text("⏳ Downloading YouTube video...")
        await download_youtube(url, chat_id, context)
    elif is_instagram:
        await download_instagram(url, chat_id, context)
    else:
        await update.message.reply_text("❌ Unsupported URL. Please send YouTube or Instagram links.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ An error occurred. Please try again.")

def main():
    if not BOT_TOKEN:
        logger.error("No bot token found in .env file!")
        return
    
    if init_instagram():
        logger.info("Instagram authenticated successfully")
    else:
        logger.warning("Instagram not authenticated - add credentials to .env")
    
    # Custom request with longer timeout (300s for file uploads)
    request = HTTPXRequest(
        connection_pool_size=8, 
        connect_timeout=60, 
        read_timeout=300,  # 5 minutes for uploads
        write_timeout=300  # 5 minutes for uploads
    )
    
    application = Application.builder().token(BOT_TOKEN).request(request).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Bot started!")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        pool_timeout=300,  # 5 minutes for all operations
    )

if __name__ == "__main__":
    main()
