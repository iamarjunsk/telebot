# Social Downloader API Documentation

REST API + WebSocket server for downloading Instagram, YouTube, and X/Twitter media.

---

## Base URL

```
http://localhost:8000
```

The host/port can be changed with environment variables:

| Variable | Default | Description |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Bind address |
| `API_PORT` | `8000` | Bind port |
| `CLEANUP_AGE_HOURS` | `48` | Temp files and old job records are deleted after this many hours |

---

## Authentication

None. The server is designed for single-user/local use. If you expose it publicly, add a reverse proxy (e.g., Nginx, Caddy, or Cloudflare Tunnel) with authentication.

---

## Supported Platforms

| Platform | URL examples |
|---|---|
| Instagram | `https://www.instagram.com/p/ABC123/`<br>`https://www.instagram.com/reel/ABC123/`<br>`https://www.instagram.com/stories/username/1234567890/` |
| YouTube | `https://www.youtube.com/watch?v=ABC123`<br>`https://youtu.be/ABC123` |
| X / Twitter | `https://x.com/user/status/1234567890`<br>`https://twitter.com/user/status/1234567890` |

---

## Endpoints

### `GET /`

Health/status check.

**Response:**

```json
{
  "status": "ok",
  "service": "Social Downloader API",
  "version": "1.1"
}
```

---

### `GET /health`

Returns server and Instagram authentication status.

**Response:**

```json
{
  "status": "ok",
  "instagram_auth": false,
  "instagram_auth_error": null,
  "instagram_anonymous_mode": true
}
```

| Field | Description |
|---|---|
| `instagram_auth` | `true` if a valid Instagram session is active |
| `instagram_auth_error` | Last auth error message, if any |
| `instagram_anonymous_mode` | `true` if running without authentication (public posts only) |

---

### `POST /download`

Queue a new download job. The job runs in the background and progress/completion is delivered via WebSocket or polled via `GET /job/{job_id}`.

**Request body:**

```json
{
  "url": "https://www.instagram.com/p/ABC123/",
  "platform": "instagram"
}
```

- `platform` is optional. If omitted, it is auto-detected from the URL.

**Response:**

```json
{
  "job_id": "a1b2c3d4",
  "status": "queued",
  "message": "Download queued for instagram"
}
```

**Error response (400):**

```json
{
  "detail": "Unsupported URL. Supported: Instagram, YouTube, X/Twitter"
}
```

---

### `GET /job/{job_id}`

Get the current status of a job.

**Response (completed example):**

```json
{
  "job_id": "a1b2c3d4",
  "status": "completed",
  "progress": 100,
  "files": [
    "/download-file/a1b2c3d4/photo_1.jpg",
    "/download-file/a1b2c3d4/video_1.mp4"
  ],
  "error": null,
  "metadata": {
    "shortcode": "ABC123",
    "username": "example",
    "caption": "Post caption here",
    "hashtags": ["photo", "travel"],
    "mentions": ["friend"],
    "likes": 1234,
    "comments": 56,
    "media_count": 2,
    "is_video": false,
    "date": "2024-01-15 10:30:00"
  }
}
```

**Status values:** `pending`, `downloading`, `completed`, `failed`

**Error response (404):**

```json
{
  "detail": "Job not found"
}
```

---

### `GET /files/{job_id}`

List downloadable files for a job with sizes.

**Response:**

```json
{
  "job_id": "a1b2c3d4",
  "status": "completed",
  "count": 2,
  "files": [
    {
      "filename": "photo_1.jpg",
      "url": "/download-file/a1b2c3d4/photo_1.jpg",
      "thumbnail_url": "/thumbnail/a1b2c3d4/photo_1.jpg",
      "size": 1543200,
      "size_human": "1.5 MB"
    },
    {
      "filename": "video_1.mp4",
      "url": "/download-file/a1b2c3d4/video_1.mp4",
      "thumbnail_url": "/thumbnail/a1b2c3d4/video_1.mp4",
      "size": 12345678,
      "size_human": "11.8 MB"
    }
  ]
}
```

---

### `GET /download-file/{job_id}/{filename}`

Download a specific file from a completed job.

**Example:**

```
GET /download-file/a1b2c3d4/photo_1.jpg
```

Returns the file as an `application/octet-stream` attachment.

---

### `GET /thumbnail/{job_id}/{filename}`

Serve a JPEG thumbnail for a file. Thumbnails are generated on first request using `ffmpeg` (max width 320px) and cached.

**Example:**

```
GET /thumbnail/a1b2c3d4/video_1.mp4
```

Returns `image/jpeg`. For images, this is a scaled-down version. For videos, this is a frame extracted from the video.

---

## WebSocket `/ws`

Connect to `ws://localhost:8000/ws` to receive real-time updates.

### Subscribe to a job

```json
{
  "type": "subscribe",
  "job_id": "a1b2c3d4"
}
```

**Server response:**

```json
{
  "type": "subscribed",
  "job_id": "a1b2c3d4",
  "status": "downloading"
}
```

### Ping

```json
{
  "type": "ping"
}
```

**Server response:**

```json
{
  "type": "pong"
}
```

### Server-sent messages

#### Progress

```json
{
  "type": "progress",
  "job_id": "a1b2c3d4",
  "message": "Trying gallery-dl...",
  "percent": 20
}
```

#### Completed

```json
{
  "type": "completed",
  "job_id": "a1b2c3d4",
  "files": [
    "/download-file/a1b2c3d4/photo_1.jpg"
  ],
  "metadata": { ... },
  "method": "instaloader",
  "caption": "Formatted caption text..."
}
```

#### Failed

```json
{
  "type": "failed",
  "job_id": "a1b2c3d4",
  "error": "Download failed. Content may be private, restricted, or requires login."
}
```

---

## Typical Flow

1. **Queue:** `POST /download` with the URL → receive `job_id`.
2. **Connect:** Open WebSocket `/ws` and subscribe to `job_id`.
3. **Wait:** Receive `progress` messages, then `completed` or `failed`.
4. **Download:** If completed, call `GET /files/{job_id}` and then `GET /download-file/{job_id}/{filename}` for each file.

Or, if you prefer polling, repeatedly call `GET /job/{job_id}` until the status is no longer `pending`/`downloading`.

---

## Job Persistence

Jobs are stored in `jobs.db` (SQLite). This means:

- Jobs survive server restarts and crashes.
- Old completed/failed records are deleted automatically after `CLEANUP_AGE_HOURS` (default 48 hours).
- Temp media files are also deleted after 48 hours.

---

## Instagram Cookie Refresh

Stories and private posts require valid Instagram cookies. The server checks `cookies.txt` on startup and also reloads it automatically every 5 minutes if it changes.

### Manual refresh

```bash
python cookie_manager.py
```

This will:
1. Try to extract cookies from Chrome/Edge/Firefox headlessly.
2. If that fails, open a visible browser window for you to log in (handles 2FA/challenge).
3. Save `cookies.txt` and an instaloader session file.
4. Validate the session.

### Automatic refresh (scheduled task)

A Windows scheduled task named **Instagram Cookie Refresh** runs `run_cookie_refresh.bat` every 4 hours.

To run it manually:

```bat
run_cookie_refresh.bat
```

To remove the scheduled task:

```cmd
schtasks /delete /tn "Instagram Cookie Refresh" /f
```

---

## Notes & Limitations

- Instagram stories and private content require a valid Instagram session (cookies or credentials).
- If Instagram auth fails, the server falls back to anonymous mode and can only download public posts.
- Large files are not split or zipped; they are served as-is.
- The server is single-user; concurrent jobs run in background tasks.
