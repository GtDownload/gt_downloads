from django.urls import path
from .views import (
    TikTokExtractorView,
    InstagramExtractorView,
    FacebookExtractorView,
    TwitterExtractorView,
    YoutubeExtractorView,
    ProxyDownloadView,
    MergedDownloadView,
    get_download_log,
    get_cached_media
)

urlpatterns = [
    path('tiktok/', TikTokExtractorView.as_view(), name='tiktok-extractor'),
    path('instagram/', InstagramExtractorView.as_view(), name='instagram-extractor'),
    path('facebook/', FacebookExtractorView.as_view(), name='facebook-extractor'),
    path('twitter/', TwitterExtractorView.as_view(), name='twitter-extractor'),
    path('youtube/', YoutubeExtractorView.as_view(), name='youtube-extractor'),
    path('proxy-download/', ProxyDownloadView.as_view(), name='proxy-download'),
    path('merged-download/', MergedDownloadView.as_view(), name='merged-download'),
    path('download-logs/', get_download_log.as_view(), name="download-logs" ),
    path('cached-media/', get_cached_media.as_view(), name="cached-media" )
]