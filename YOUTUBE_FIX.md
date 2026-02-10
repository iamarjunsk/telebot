# Fixing YouTube 403 Forbidden Error

If you get "HTTP Error 403: Forbidden" when downloading YouTube videos, YouTube is blocking the request. Here are the solutions:

## Solution 1: Use Browser Cookies (Recommended)

YouTube blocks requests without valid cookies. Export your browser cookies:

### Option A: Using cookies.txt extension

1. Install the "cookies.txt" extension in Chrome/Firefox
2. Go to YouTube and make sure you're logged in
3. Click the extension → "Export" → Save as `cookies.txt` in the bot folder
4. The bot will automatically use this file

### Option B: Using yt-dlp directly with cookies

```bash
source venv/bin/activate

# Download with cookies from browser
yt-dlp --cookies-from-browser firefox "URL"

# Or with cookies file
yt-dlp --cookies cookies.txt "URL"
```

## Solution 2: Use a Different Network

YouTube sometimes blocks specific IPs. Try:
- Using mobile hotspot
- Using a VPN (connect to US/EU servers)
- Waiting a few hours and retrying

## Solution 3: Login to YouTube in Browser

1. Open YouTube in your browser
2. Make sure you're logged in
3. Watch any video for a few seconds
4. Try the download again

## Solution 4: Update yt-dlp

```bash
source venv/bin/activate
pip install -U yt-dlp
```

YouTube changes frequently, so keeping yt-dlp updated is important.

## What the bot already does

The bot now includes:
- Latest yt-dlp version
- Mobile client emulation (less likely to be blocked)
- Proper HTTP headers
- 720p quality limit (faster downloads, less detection)

If you still get 403 errors, **using browser cookies (Solution 1)** is the most reliable fix.
