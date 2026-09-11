import hashlib
import os
import re
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from yt_dlp.networking.impersonate import ImpersonateTarget

import requests
import yt_dlp
from django.conf import settings
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CachedMedia, DownloadLog
from .serializers import (
    CachedMediaSerializer,
    DownloadLogSerializer,
    VideoRequestSerializer,
)

# ---------------------------------------------------------------------------
# GLOBAL CONFIG & MAPPINGS
# ---------------------------------------------------------------------------

REQUESTED_QUALITIES = {
    "480": 480,
    "hd": 720,
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

PLATFORM_REFERERS = {
    "tiktok": "https://www.tiktok.com/",
    "facebook": "https://www.facebook.com/",
    "twitter": "https://x.com/",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://www.youtube.com/",
}

LOG_STATUS_MAP = {
    "success": "SUCCESS",
    "success_cached": "SUCCESS",
    "failed": "FAILED",
    "pending": "PENDING",
}

# ← FIX: The YouTube client list that works best on datacenter IPs (Render, AWS, etc.)
# "tv" is currently the most reliable. "web_safari" mimics Safari (less fingerprinted).
# Avoid "default" — it gets bot-blocked instantly on cloud IPs.
YOUTUBE_PLAYER_CLIENTS = ["tv", "web_safari", "mweb"]

# ← FIX: Optional residential proxy for YouTube. Set YTDLP_YOUTUBE_PROXY in Render
# env vars to route YouTube requests through a residential IP. Leave blank to skip.
YOUTUBE_PROXY = os.getenv("YTDLP_YOUTUBE_PROXY", "").strip() or None

# ← FIX: Optional PO Token provider (bgutil). Set YTDLP_PO_TOKEN in Render env vars.
# Example value: "bgutilhttp:base_url=http://your-provider.onrender.com:4416"
YOUTUBE_PO_TOKEN = os.getenv("YTDLP_PO_TOKEN", "").strip() or None


# ---------------------------------------------------------------------------
# UTILITY HELPERS
# ---------------------------------------------------------------------------

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def safe_filename(title: str, fallback: str = "video") -> str:
    title = str(title or "").strip()
    title = re.sub(r'[\\/*?:"<>|]', "", title)
    title = re.sub(r"[\x00-\x1f\x7f]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    title = title.rstrip(". ")
    return (title or fallback)[:150]


def forward_format_headers(fmt: dict) -> dict:
    headers = fmt.get("http_headers") or {}
    allowed = {"Referer", "User-Agent", "Origin", "Cookie"}
    return {k: v for k, v in headers.items() if k in allowed and v}


# ---------------------------------------------------------------------------
# BASE EXTRACTOR VIEW
# ---------------------------------------------------------------------------

class BaseExtractorView(APIView):
    platform_name = "unknown"
    # ← FIX: Only TikTok uses the shared cookies.txt. Public platforms do NOT
    # inherit personal cookies, otherwise YouTube gets flagged instantly.
    uses_cookies = False

    def sanitize_url(self, raw_url: str) -> str:
        if "youtube.com" in raw_url or "youtu.be" in raw_url:
            parsed = urlparse(raw_url)
            query_params = parse_qs(parsed.query)
            video_id = query_params.get("v", [None])[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        return raw_url

    def get_cookie_file(self) -> str | None:
        # ← FIX: Block cookie usage on non-TikTok platforms
        if not self.uses_cookies:
            return None

        value = getattr(settings, "YTDLP_COOKIE_FILE", None)
        if not value:
            return None
        path = os.path.expanduser(str(value))
        if os.path.isfile(path):
            return path
        base_dir_path = os.path.join(settings.BASE_DIR, value)
        if os.path.isfile(base_dir_path):
            return base_dir_path
        return None

    def log_request(self, original_url: str, log_status: str, duration: float):
        try:
            serializer = DownloadLogSerializer(data={
                "platform": self.platform_name,
                "original_url": original_url,
                "status": LOG_STATUS_MAP.get(log_status, "PENDING"),
                "request_duration": duration,
            })
            if serializer.is_valid():
                serializer.save()
            else:
                print("DownloadLog Validation Error:", serializer.errors)
        except Exception as e:
            print("DownloadLog Exception:", e)

    def build_proxy_url(self, request, direct_url: str, platform: str, http_headers=None) -> str:
        proxy_token = hashlib.sha256(f"{direct_url}-{time.time()}".encode()).hexdigest()[:32]

        proxy_data = {
            "url": direct_url,
            "platform": platform,
            "headers": http_headers or {},
        }

        from django.core.cache import cache
        cache.set(f"video_proxy:{proxy_token}", proxy_data, timeout=600)

        try:
            proxy_path = reverse("proxy-download")
        except Exception:
            proxy_path = "/api/proxy-download/"

        return request.build_absolute_uri(f"{proxy_path}?token={proxy_token}")

    def get_ydl_options(self) -> dict:
        opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "allow_playlist_files": False,
            "http_headers": {
                "User-Agent": USER_AGENT,
            },
        }
        cookie_file = self.get_cookie_file()
        if cookie_file:
            opts["cookiefile"] = cookie_file
        return opts

    def select_best_format(self, formats, target_height=None):
        best_format = None
        best_score = -1

        for fmt in formats:
            if not fmt.get("url"):
                continue

            vcodec = fmt.get("vcodec") or ""
            acodec = fmt.get("acodec")

            if vcodec == "none" or acodec in ("none", None):
                continue

            height = fmt.get("height") or 0
            if height == 0:
                continue

            ext = fmt.get("ext", "")
            score = height

            if target_height and height > target_height:
                score = target_height - (height - target_height) * 2

            if ext == "mp4":
                score += 10
            if "avc1" in vcodec or "h264" in vcodec.lower():
                score += 5

            if score > best_score:
                best_score = score
                best_format = fmt

        return best_format

    def post(self, request):
        start_time = time.time()

        req_serializer = VideoRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(
                {"error": "Invalid URL or request payload.", "details": req_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_url = self.sanitize_url(req_serializer.validated_data["url"])
        url_hash = req_serializer.get_url_hash()
        requested_resolution = req_serializer.validated_data.get("resolution", 720)

        # ========== CACHING (12 hours expiration) ==========
        cached_item = CachedMedia.objects.filter(
            url_hash=url_hash,
            expires_at__gt=timezone.now(),
        ).first()

        if cached_item:
            request_duration = round(time.time() - start_time, 3)
            self.log_request(target_url, "success_cached", request_duration)

            cached_data = CachedMediaSerializer(cached_item).data

            if cached_data["platform"] == "youtube":
                try:
                    merge_path = reverse("merged-download")
                except Exception:
                    merge_path = "/api/merged-download/"
                quality_key = "480" if requested_resolution <= 480 else "hd"
                encoded_url = quote(target_url, safe="")
                download_url = request.build_absolute_uri(f"{merge_path}?url={encoded_url}&quality={quality_key}")
            else:
                download_url = self.build_proxy_url(
                    request,
                    cached_data["direct_download_url"],
                    cached_data["platform"],
                )

            resolution_label = (
                f"{cached_data.get('resolution')}p"
                if cached_data.get("resolution")
                else f"{requested_resolution}p"
            )

            return Response({
                "url_hash": cached_data["url_hash"],
                "platform": cached_data["platform"],
                "original_url": cached_data["original_url"],
                "title": cached_data["title"],
                "thumbnail_url": cached_data["thumbnail_url"],
                "duration": cached_data["duration"],
                "resolution": resolution_label,
                "cached": True,
                "download_url": download_url,
            })

        # YouTube Handling: Extract real metadata via yt-dlp first before returning merge URL
        if self.platform_name == "youtube":
            ydl_opts = self.get_ydl_options()
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=False)
            except Exception:
                info = {}

            title = info.get("title") or "YouTube Video"
            thumbnail = info.get("thumbnail") or info.get("url") or ""
            duration = info.get("duration")

            try:
                merge_path = reverse("merged-download")
            except Exception:
                merge_path = "/api/merged-download/"

            quality_key = "480" if requested_resolution <= 480 else "hd"
            encoded_url = quote(target_url, safe="")
            merged_url = request.build_absolute_uri(
                f"{merge_path}?url={encoded_url}&quality={quality_key}"
            )

            request_duration = round(time.time() - start_time, 3)
            self.log_request(target_url, "success", request_duration)

            try:
                CachedMedia.objects.update_or_create(
                    url_hash=url_hash,
                    defaults={
                        "platform": "youtube",
                        "original_url": target_url,
                        "title": title[:500],
                        "thumbnail_url": thumbnail,
                        "direct_download_url": merged_url,
                        "duration": duration,
                        "expires_at": timezone.now() + timedelta(hours=12),
                    },
                )
            except Exception:
                pass

            return Response({
                "url_hash": url_hash,
                "platform": "youtube",
                "original_url": target_url,
                "title": title,
                "thumbnail_url": thumbnail,
                "duration": duration,
                "resolution": f"{requested_resolution}p",
                "cached": False,
                "download_url": merged_url,
            })

        # ========== FRESH EXTRACTION (Other Platforms) ==========
        ydl_opts = self.get_ydl_options()

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
        except Exception as e:
            request_duration = round(time.time() - start_time, 3)
            self.log_request(target_url, "failed", request_duration)

            return Response({
                "error": f"Failed to extract video from {self.platform_name.title()}",
                "details": str(e).strip() or type(e).__name__,
            }, status=status.HTTP_400_BAD_REQUEST)

        request_duration = round(time.time() - start_time, 3)
        self.log_request(target_url, "success", request_duration)

        title = info.get("title") or info.get("description") or "video"
        thumbnail = info.get("thumbnail") or info.get("url")
        duration = info.get("duration")
        formats = info.get("formats", [])

        best_format = self.select_best_format(formats, requested_resolution)

        if not best_format and formats:
            for fmt in formats:
                if fmt.get("url") and fmt.get("vcodec") != "none" and fmt.get("acodec") not in ("none", None):
                    best_format = fmt
                    break

        raw_cdn_url = best_format.get("url") if best_format else info.get("url")

        if not raw_cdn_url:
            return Response(
                {"error": "Could not locate a valid downloadable stream URL for this video."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        format_headers = (best_format.get("http_headers") if best_format else None) or ydl_opts.get("http_headers")

        final_height = best_format.get("height") if best_format else info.get("height")
        actual_resolution = final_height or requested_resolution
        res_label = f"{actual_resolution}p" if actual_resolution else "HD"

        proxied_download_url = self.build_proxy_url(
            request,
            raw_cdn_url,
            self.platform_name,
            http_headers=format_headers,
        )

        try:
            CachedMedia.objects.update_or_create(
                url_hash=url_hash,
                defaults={
                    "platform": self.platform_name,
                    "original_url": target_url,
                    "title": title[:500],
                    "thumbnail_url": thumbnail,
                    "direct_download_url": raw_cdn_url,
                    "duration": duration,
                    "expires_at": timezone.now() + timedelta(hours=12),
                },
            )
        except Exception:
            pass

        return Response({
            "url_hash": url_hash,
            "platform": self.platform_name,
            "original_url": target_url,
            "title": title,
            "thumbnail_url": thumbnail,
            "duration": duration,
            "cached": False,
            "requested_resolution": requested_resolution,
            "resolution": res_label,
            "download_url": proxied_download_url,
        })


# ================================================================
# PLATFORM EXTRACTORS
# ================================================================
class TikTokExtractorView(BaseExtractorView):
    platform_name = "tiktok"
    # ← FIX: TikTok NEEDS cookies (tt_chain_token) to bypass the 403
    uses_cookies = True

    def get_ydl_options(self) -> dict:
        opts = super().get_ydl_options()
        opts.update({
            "format": "best[height>=720]/best",
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": PLATFORM_REFERERS["tiktok"],
                "Accept-Language": "en-US,en;q=0.9",
            },
        })
        return opts


class InstagramExtractorView(BaseExtractorView):
    platform_name = "instagram"

    def get_ydl_options(self) -> dict:
        opts = super().get_ydl_options()
        opts.update({
            "format": "best[height>=720][height<=1080]/best[height>=720]/best",
            # ← FIX: Instagram CDN blocks non-browser TLS fingerprints
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": PLATFORM_REFERERS["instagram"],
                "Accept-Language": "en-US,en;q=0.9",
            },
        })
        return opts


class FacebookExtractorView(BaseExtractorView):
    platform_name = "facebook"

    def get_ydl_options(self) -> dict:
        opts = super().get_ydl_options()
        opts.update({
            "format": "best[height>=720][height<=1080]/best[height>=720]/best",
            # ← FIX: Facebook 403s on datacenter IPs without impersonation
            "impersonate": ImpersonateTarget.from_str("chrome"),
            # ← FIX: Large files (>500MB) on Facebook return 403 during download.
            # Forcing 250MB chunks bypasses this bug.
            "http_chunk_size": 250 * 1024 * 1024,
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": PLATFORM_REFERERS["facebook"],
                "Accept-Language": "en-US,en;q=0.9",
            },
        })
        return opts


class TwitterExtractorView(BaseExtractorView):
    platform_name = "twitter"

    def get_ydl_options(self) -> dict:
        opts = super().get_ydl_options()
        opts.update({
            "format": "best[height>=720][height<=1080]/best[height>=720]/best",
            # ← FIX: Twitter/X CDN checks TLS fingerprint
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": PLATFORM_REFERERS["twitter"],
                "Accept-Language": "en-US,en;q=0.9",
            },
        })
        return opts


class YoutubeExtractorView(BaseExtractorView):
    platform_name = "youtube"
    # Note: YouTube does NOT use cookies. Public endpoint must not share personal cookies.

    def get_ydl_options(self) -> dict:
        opts = super().get_ydl_options()
        opts.update({
            "noplaylist": True,
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "format": "bestvideo+bestaudio/best",
            "format_sort": ["res:720", "ext:mp4:m4a", "codec:h264"],
            "merge_output_format": "mp4",
            "extractor_args": {
                "youtube": {
                    # ← FIX: This is the critical change for production
                    "player_client": YOUTUBE_PLAYER_CLIENTS,
                }
            },
        })

        # ← FIX: Route YouTube through a residential proxy if configured
        if YOUTUBE_PROXY:
            opts["proxy"] = YOUTUBE_PROXY

        # ← FIX: Attach PO Token if configured
        if YOUTUBE_PO_TOKEN:
            opts["extractor_args"]["youtube"]["po_token"] = YOUTUBE_PO_TOKEN

        return opts


# ================================================================
# MERGED DOWNLOAD VIEW (YouTube FFmpeg Processing)
# ================================================================
class MergedDownloadView(APIView):
    def get(self, request):
        original_url = request.query_params.get("url", "").strip()
        quality = request.query_params.get("quality", "hd").lower().strip()

        if not original_url:
            return HttpResponse("Missing video URL.", status=400)

        target_height = REQUESTED_QUALITIES.get(quality, 720)

        if not ffmpeg_available():
            return HttpResponse("FFmpeg is required for merged downloads.", status=503)

        temp_dir = Path(tempfile.mkdtemp(prefix="gtdownload_"))
        output_template = str(temp_dir / "%(title).120s.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": f"bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]",
            "format_sort": ["res", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": "https://www.youtube.com/",
            },
            "extractor_args": {
                "youtube": {
                    # ← FIX: Same client list as YoutubeExtractorView
                    "player_client": YOUTUBE_PLAYER_CLIENTS,
                }
            },
        }

        # ← FIX: DO NOT pass cookiefile here. YouTube is a public endpoint and
        # passing TikTok cookies (or any personal cookies) causes instant
        # "Sign in to confirm you're not a bot" blocks.
        # Cookies are for TikTok only.

        # ← FIX: Apply residential proxy if configured
        if YOUTUBE_PROXY:
            ydl_opts["proxy"] = YOUTUBE_PROXY

        # ← FIX: Apply PO Token if configured
        if YOUTUBE_PO_TOKEN:
            ydl_opts["extractor_args"]["youtube"]["po_token"] = YOUTUBE_PO_TOKEN

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(original_url, download=True)

            title = safe_filename(info.get("title", "video") if info else "video")
            candidates = sorted(
                temp_dir.glob("*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )

            media_file = next(
                (item for item in candidates if item.is_file() and item.suffix.lower() == ".mp4"),
                None,
            )

            if media_file is None:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return HttpResponse("FFmpeg processing failed to output an MP4 file.", status=500)

            final_file = temp_dir / f"{title}_gtdownload.mp4"
            if media_file != final_file:
                media_file.rename(final_file)

            response = FileResponse(
                open(final_file, "rb"),
                as_attachment=True,
                filename=final_file.name,
                content_type="video/mp4",
            )
            response._resource_closers.append(
                lambda: shutil.rmtree(temp_dir, ignore_errors=True)
            )
            return response

        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            error_text = str(exc).strip() or type(exc).__name__

            # ← FIX: Provide a clearer production error message
            if "Sign in to confirm you're not a bot" in error_text:
                return HttpResponse(
                    "YouTube blocked this server request. Configure YTDLP_YOUTUBE_PROXY "
                    "or YTDLP_PO_TOKEN on Render to bypass bot detection.",
                    status=503,
                )

            return HttpResponse(f"Error processing video stream: {error_text}", status=500)


# ================================================================
# PROXY DOWNLOAD VIEW
# ================================================================
class ProxyDownloadView(APIView):
    def get(self, request):
        token = request.query_params.get("token")
        if not token:
            return HttpResponse("Missing token parameter.", status=400)

        from django.core.cache import cache
        proxy_data = cache.get(f"video_proxy:{token}")
        if not proxy_data:
            return HttpResponse("Invalid or expired token.", status=404)

        video_url = proxy_data["url"]
        platform = (proxy_data.get("platform") or "").lower()
        extra_headers = proxy_data.get("headers") or {}

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }

        if platform == "tiktok" or "tiktok" in video_url.lower() or "muscdn" in video_url.lower():
            headers["Referer"] = "https://www.tiktok.com/"
            headers["Origin"] = "https://www.tiktok.com"
        elif platform == "instagram" or "cdninstagram" in video_url.lower():
            headers["Referer"] = "https://www.instagram.com/"
            headers["Origin"] = "https://www.instagram.com"
        elif platform == "youtube" or "googlevideo" in video_url.lower():
            headers["Referer"] = "https://www.youtube.com/"
        elif platform == "facebook" or "fbcdn" in video_url.lower():
            headers["Referer"] = "https://www.facebook.com/"
        elif platform in ("twitter", "x") or "twimg" in video_url.lower():
            headers["Referer"] = "https://x.com/"
        else:
            headers["Referer"] = "https://www.google.com/"

        headers.update(extra_headers)

        client_range = request.headers.get("Range")
        if client_range:
            headers["Range"] = client_range

        try:
            response = requests.get(
                video_url,
                headers=headers,
                stream=True,
                timeout=60,
                allow_redirects=True,
            )

            if response.status_code not in (200, 206):
                response.close()
                return HttpResponse(
                    f"CDN Access Denied (Status Code: {response.status_code})",
                    status=response.status_code,
                )

            def stream_file():
                try:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            yield chunk
                finally:
                    response.close()

            custom_filename = "video_gtdownload.mp4"

            cached_obj = CachedMedia.objects.filter(
                direct_download_url__icontains=video_url[:80]
            ).first()

            if cached_obj and cached_obj.title:
                safe_title = re.sub(r'[\\/*?:"<>|\n\r\t]', "", cached_obj.title).strip()
                if safe_title:
                    custom_filename = f"{safe_title[:60]}_gtdownload.mp4"

            content_type = response.headers.get("Content-Type") or "video/mp4"
            if "video" not in content_type.lower() and "octet-stream" not in content_type.lower():
                content_type = "video/mp4"

            streaming_response = StreamingHttpResponse(
                stream_file(),
                status=response.status_code,
                content_type=content_type,
            )

            streaming_response["Accept-Ranges"] = "bytes"

            if "Content-Length" in response.headers:
                streaming_response["Content-Length"] = response.headers["Content-Length"]

            if "Content-Range" in response.headers:
                streaming_response["Content-Range"] = response.headers["Content-Range"]

            streaming_response["Content-Disposition"] = (
                f'attachment; filename="{custom_filename}"; '
                f"filename*=UTF-8''{quote(custom_filename)}"
            )

            streaming_response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            streaming_response["Pragma"] = "no-cache"
            streaming_response["Expires"] = "0"

            return streaming_response

        except requests.exceptions.Timeout:
            return HttpResponse("Request timeout while fetching video.", status=504)
        except requests.exceptions.RequestException as e:
            return HttpResponse(f"Error fetching video: {str(e)}", status=500)
        except Exception as e:
            return HttpResponse(f"Unexpected error: {str(e)}", status=500)