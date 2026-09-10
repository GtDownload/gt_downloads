# GT_Downloads API

## Overview

GT_Downloads is a robust, multi-platform media downloader API built using Django and `yt-dlp`. It allows public users and frontend integrations to smoothly extract video streams, metadata, and thumbnails from YouTube, Instagram, X (Twitter), Facebook, and TikTok without requiring personal cookie configurations.

---

## Features & Core Architecture

-   **Multi-Platform Support**: Dedicated endpoints for YouTube, TikTok, Instagram, Facebook, and X (Twitter).
-   **Database Caching (`CachedMedia`)**: Incoming URLs are sanitized and hashed using SHA-256 (`url_hash`). Valid extractions are cached with a 12-hour expiration window to completely eliminate redundant external network requests.
-   **Activity Logging (`DownloadLog`)**: Tracks platform type, original URL, request duration, and operational status (`SUCCESS`, `FAILED`, `PENDING`).
-   **Stream Handling & Merging**: Automatically routes YouTube adaptive video and audio tracks through system-level FFmpeg to stitch high-definition MP4 files on the fly.
-   **Secure Streaming Proxy (`ProxyDownloadView`)**: Server-side proxying that forwards exact headers, supports `Range` requests, and prevents SSRF security exploits.

---

## API Endpoints Reference

Base URL: `https://gt-downloads.onrender.com/api/`

### 1. Platform Extraction Endpoints

All platform extraction endpoints accept an HTTP `POST` request with a JSON payload.

| Endpoint Path     | Platform    | HTTP Method | Description                                                                   |
| :---------------- | :---------- | :---------- | :---------------------------------------------------------------------------- |
| `/api/tiktok/`    | TikTok      | POST        | Extracts TikTok media metadata and returns a proxied stream link.             |
| `/api/instagram/` | Instagram   | POST        | Resolves direct Instagram media CDN links.                                    |
| `/api/facebook/`  | Facebook    | POST        | Extracts public Facebook video streams.                                       |
| `/api/twitter/`   | X / Twitter | POST        | Resolves media content embedded within tweets.                                |
| `/api/youtube/`   | YouTube     | POST        | Routes YouTube links directly to the adaptive FFmpeg stream merging workflow. |

#### **Request Body Schema**

```json
{
  "url": "[https://www.youtube.com/watch?v=EXAMPLE_ID](https://www.youtube.com/watch?v=EXAMPLE_ID)",
  "resolution": 720
}
Successful Extraction Response (200 OK)
JSON
{
  "url_hash": "a1b2c3d4e5f6...",
  "platform": "youtube",
  "original_url": "[https://www.youtube.com/watch?v=EXAMPLE_ID](https://www.youtube.com/watch?v=EXAMPLE_ID)",
  "title": "Sample Video Title",
  "thumbnail_url": "[https://img.youtube.com/vi/EXAMPLE_ID/hqdefault.jpg](https://img.youtube.com/vi/EXAMPLE_ID/hqdefault.jpg)",
  "duration": 245,
  "cached": false,
  "filename": "Sample_Video_Title_gtdownload.mp4",
  "resolutions": [
    {
      "key": "hd",
      "label": "HD",
      "resolution": 720,
      "download_url": "[https://gt-downloads.onrender.com/api/merged-download/?url=...&quality=hd](https://gt-downloads.onrender.com/api/merged-download/?url=...&quality=hd)",
      "merge_required": true
    }
  ]
}
2. Utility & Processing Endpoints
Proxy Download (GET /api/proxy-download/)

Securely proxies remote CDN resources server-side to prevent CORS blocks and 403 Forbidden restrictions.

Query Parameters: url (encoded media source) and optional forwarded headers (h_referer, h_user_agent).

Merged Download (GET /api/merged-download/)

Triggers FFmpeg execution to stitch separate YouTube video and audio streams into a unified MP4 file.

Query Parameters: url (target YouTube link) and quality (480 or hd).

Local Installation & Setup
Clone the Repository

Bash
git clone [https://github.com/GtDownload/gt_downloads.git](https://github.com/GtDownload/gt_downloads.git)
cd gt_downloads
Create & Activate a Virtual Environment

Bash
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
Install Dependencies

Bash
pip install -r requirements.txt
Apply Database Migrations

Bash
python manage.py makemigrations
python manage.py migrate
Run the Development Server

Bash
python manage.py runserver
```
