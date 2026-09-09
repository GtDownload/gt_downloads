from rest_framework import serializers
import hashlib
from .models import CachedMedia, DownloadLog


class VideoRequestSerializer(serializers.Serializer):
    url = serializers.URLField(required=True)
    resolution = serializers.IntegerField(required=False, min_value=480, max_value=1080, default=720)
    
    def get_url_hash(self):
        # Include resolution in hash for different cached versions
        url = self.validated_data['url']
        resolution = self.validated_data.get('resolution', 720)
        content = f"{url}|{resolution}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()


class DownloadLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DownloadLog
        fields = '__all__'


class CachedMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CachedMedia
        fields = '__all__'