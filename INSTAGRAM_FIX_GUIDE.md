# Instagram Downloader Fix — Complete Guide

## 🔴 Why Your Bot Can't Download Stories / Age-Restricted Content

### Root Cause: Dead Session
Your `session_halo_aju2_` file is **1,218 hours old** (51 days). Instagram sessions expire after ~6-12 hours. Without a valid session, the bot cannot access:
- **Stories** (always require authentication)
- **Age-restricted posts** (require `sessionid` + `ds_user_id` cookies)
- **Private-ish content** (requires active login)

### Missing Critical Cookies
Your `cookies.txt` has these cookies:
- `csrftoken` ✅
- `datr`, `ig_did`, `mid`, `wd` ✅
- **`sessionid`** ❌ MISSING
- **`ds_user_id`** ❌ MISSING

These two are **mandatory** for any authenticated Instagram API call.

---

## ✅ Solution 1: Interactive Browser Login (Recommended — Run This Now)

This opens a real Chrome window, you log in, and it extracts the cookies automatically.

### Step 1: Make sure you're in the bot's venv
```bash
cd /c/Users/skang/telebot
source venv/Scripts/activate
```

### Step 2: Run the fixer
```bash
python fix_session.py
```

### Step 3: Follow the prompts
1. Press ENTER to open Chrome
2. Log into Instagram with your account
3. If 2FA appears, enter the code
4. Wait until you see your feed
5. Press ENTER in the terminal

### Step 4: Restart your bot
```bash
python bot.py
```

---

## ✅ Solution 2: Manual Cookie Export (If you prefer)

If you don't want to use the interactive script:

### Option A: Export from Chrome Extension
1. Install "Get cookies.txt LOCALLY" extension in Chrome
2. Go to instagram.com (make sure you're logged in)
3. Click the extension → Export as Netscape
4. Save to `C:/Users/skang/telebot/cookies.txt`
5. Restart bot

### Option B: Use extract_cookies.py (already in your bot)
```bash
cd /c/Users/skang/telebot
python extract_cookies.py
```

---

## ✅ Solution 3: Auto-Refresh (Set Up After Fix)

I've created `auto_refresh_cookies.py` that tries to pull fresh cookies from your browser every 4 hours. **This only works if you stay logged into Instagram in Chrome/Edge.**

### Run manually:
```bash
python auto_refresh_cookies.py
```

### Or let the cron job handle it (already scheduled):
A cron job `ig-cookie-refresh` runs every 4 hours and will notify you if cookies go stale.

---

## 🛠️ What I Already Fixed in Your Bot

1. **Reduced session expiry threshold** from 12h → 6h (bot now treats old sessions as invalid sooner)
2. **Added cookies.txt priority check** — bot now checks if cookies.txt has `sessionid` + `ds_user_id` before trying anything else
3. **Installed missing tools** in your venv:
   - `gallery-dl` (best for stories)
   - `playwright` (for headless/interactive login)
   - `browser_cookie3` (extract from system browser)
   - `curl_cffi` (browser impersonation)

---

## 📋 Quick Checklist

| Step | Action | Status |
|------|--------|--------|
| 1 | Run `python fix_session.py` in venv | ⬜ DO THIS NOW |
| 2 | Log into Instagram in the browser window | ⬜ |
| 3 | Press ENTER after feed loads | ⬜ |
| 4 | Restart bot (`python bot.py`) | ⬜ |
| 5 | Test with a story URL | ⬜ |
| 6 | Keep Instagram logged in in Chrome for auto-refresh | ⬜ |

---

## 🔍 How Other Bots Do It

Public Telegram bots that download age-restricted Instagram content typically use:
1. **Shared account pools** — they maintain 10-100 Instagram accounts and rotate sessions
2. **Proxy + fresh sessions** — each request uses a new residential IP + freshly logged-in session
3. **Third-party APIs** — some use paid services like `savefrom.net`, `snapinsta.app`, etc. (unreliable, break often)

Your bot's multi-fallback approach (gallery-dl → GraphQL → yt-dlp → curl_cffi → instaloader) is actually **better** than most — the only issue was the stale session.

---

## 🚨 If Interactive Login Doesn't Work

If `fix_session.py` fails or you can't use interactive login:

1. **Log into Instagram in Chrome on this machine**
2. **Use a cookie exporter extension** to save cookies as `cookies.txt`
3. **Place it at `C:/Users/skang/telebot/cookies.txt`**
4. **Restart bot**

The bot will detect the fresh cookies and use them.

---

## 📁 Files Created/Modified

| File | Purpose |
|------|---------|
| `fix_session.py` | Interactive login + cookie extraction |
| `auto_refresh_cookies.py` | Headless cookie refresh from browser |
| `refresh_cookies.py` | Alternative refresh script |
| `diagnose.py` | Diagnostic tool to test all methods |
| `bot.py` | Modified session logic (6h expiry, cookie priority) |

---

## 🎯 Next Steps

**Right now:**
```bash
cd /c/Users/skang/telebot
venv/Scripts/python fix_session.py
```

Then follow the prompts. After that, stories and age-restricted content will work.

**For long-term:** Keep Instagram logged in in Chrome so `auto_refresh_cookies.py` can pull fresh cookies automatically.
