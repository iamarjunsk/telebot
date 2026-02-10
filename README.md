# Media Downloader Bot

Telegram bot for downloading YouTube videos and Instagram content.

## Features

- 📺 Download YouTube videos (any size, auto-compressed if >50MB)
- 📸 Download Instagram posts, reels, stories, and IGTV
- 🔐 Instagram authentication support (for private content)
- ⚡ No artificial delays or restrictions
- 🗜️ Automatic video compression for large files

## Setup

1. **Install ffmpeg (required for large videos):**

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH

2. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure .env file:**
```env
tg_bot_token=your_telegram_bot_token
ig_username=your_instagram_username
ig_pass=your_instagram_password
```

3. **Get Telegram Bot Token:**
- Message [@BotFather](https://t.me/BotFather) on Telegram
- Create a new bot with `/newbot`
- Copy the token and paste it in `.env` as `tg_bot_token`

4. **Create Instagram session (IMPORTANT):**

Instagram authentication often fails on first try. Run this script first:
```bash
python create_session.py
```

This creates a session file that the bot will use. If you see "Session created successfully", you can proceed.

⚠️ **Note:** If you have Two-Factor Authentication (2FA) enabled on Instagram, you may need to disable it temporarily.

5. **Run the bot:**
```bash
python bot.py
```

## Usage

- Send `/start` to see welcome message
- Send `/status` to check Instagram login status
- Send any YouTube or Instagram link to download

## Supported URLs

**YouTube:**
- Regular videos: `youtube.com/watch?v=...`
- Shorts: `youtube.com/shorts/...`
- Share links: `youtu.be/...`

**Instagram:**
- Posts: `instagram.com/p/...`
- Reels: `instagram.com/reel/...`
- Stories: `instagram.com/stories/...`
- IGTV: `instagram.com/tv/...`

## Notes

- **Large YouTube videos (>50MB) are automatically compressed** to fit Telegram's limits
- Instagram requires login for stories and private accounts
- Downloads are cleaned up after sending
- Quality is maintained as best as possible during compression

## Troubleshooting

### Instagram Authentication Issues

**Error: "JSON Query to graphql/query: HTTP error code 401"**

This means Instagram authentication failed. Solutions:

1. **Check credentials**: Make sure `ig_username` and `ig_pass` in `.env` are correct
2. **Run session creator**: `python create_session.py`
3. **Disable 2FA**: If you have two-factor auth enabled, disable it temporarily
4. **Browser login**: Login to instagram.com from the same device first
5. **Wait**: Instagram might be rate-limiting, wait 10-15 minutes and try again
6. **Session file**: If `session-<username>` file exists, delete it and retry

**Still not working?**
- Make sure you can login to instagram.com manually
- Try changing your Instagram password
- Use a different Instagram account
- Instagram may have blocked your IP for automated logins
