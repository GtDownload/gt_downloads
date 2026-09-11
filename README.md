# GT_Downloads API Documentation

This technical reference outlines the core endpoints, request payload structures, database caching architecture, and streaming proxy mechanisms for the multi-platform media downloader API.

---

**Base Configuration & Architecture**

-   **Base URL:** `[https://gt-downloads.onrender.com/api/extract/](https://gt-downloads.onrender.com/api/extract/)`
-   **Framework:** Django (v6.1) & Django REST Framework (DRF)
-   **Supported Platforms:** TikTok, Instagram, Facebook, X (Twitter), and YouTube.

---

**API Endpoints Reference**

### 1. Platform Extraction Endpoints

All platform extraction endpoints accept an HTTP `POST` request with a JSON payload containing the target resource link.

| Endpoint Path             | Platform    | HTTP Method | Description                                                                   |
| ------------------------- | ----------- | ----------- | ----------------------------------------------------------------------------- |
| `/api/extract/tiktok/`    | TikTok      | POST        | Extracts TikTok media metadata and returns a proxied stream link.             |
| `/api/extract/instagram/` | Instagram   | POST        | Resolves direct Instagram media CDN links.                                    |
| `/api/extract/facebook/`  | Facebook    | POST        | Extracts public Facebook video streams.                                       |
| `/api/extract/twitter/`   | X / Twitter | POST        | Resolves media content embedded within tweets.                                |
| `/api/extract/youtube/`   | YouTube     | POST        | Routes YouTube links directly to the adaptive FFmpeg stream merging workflow. |

#### **Request Body Schema**

```json
{
	"url": "https://www.youtube.com/watch?v=EXAMPLE_ID",
	
}
```

#### **Successful Extraction Response (`200 OK`)**

```json
{
	"url_hash": "a1b2c3d4e5f6...",
	"platform": "youtube",
	"original_url": "https://www.youtube.com/watch?v=EXAMPLE_ID",
	"title": "Sample Video Title",
	"thumbnail_url": "https://img.youtube.com/vi/EXAMPLE_ID/hqdefault.jpg",
	"duration": 245,
	"cached": false,
	"download_url": "https://gt-downloads.onrender.com/api/merged-download/?url=...&quality=hd"
}
```

### 2. Management & Monitoring Endpoints

-   **Download Logs (`/api/extract/download-logs/`)**
-   `GET`: Retrieves a list of all recorded extraction logs.
-   `DELETE`: Wipes all recorded download logs from the database.

-   **Cached Media (`/api/extract/cached-media/`)**
-   `GET`: Lists all currently cached media records.
-   `DELETE`: Purges all active database media caches.

### 3. Utility & Processing Endpoints

-   **Proxy Download (`/api/proxy-download/`)**
-   Securely proxies remote CDN resources server-side to prevent CORS blocks and 403 Forbidden restrictions.
-   **Query Parameters:** `url` (encoded media source) and optional forwarded headers (`h_referer`, `h_user_agent`).

-   **Merged Download (`/api/merged-download/`)**
-   Triggers FFmpeg execution to stitch separate YouTube video and audio streams into a unified MP4 file.
-   **Query Parameters:** `url` (target YouTube link) and `quality` (`480` or `hd`).
