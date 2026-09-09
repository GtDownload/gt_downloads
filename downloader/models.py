import uuid
from django.db import models


class DownloadLog(models.Model):
    STATUS_CHOICES = [
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('PENDING', 'Pending'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=50)
    original_url = models.URLField(max_length=2000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    request_duration = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.platform.capitalize()} - {self.status} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"


class CachedMedia(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url_hash = models.CharField(max_length=64, unique=True, db_index=True)
    platform = models.CharField(max_length=50)
    original_url = models.URLField(max_length=2000)
    title = models.CharField(max_length=500, null=True, blank=True)
    thumbnail_url = models.URLField(max_length=2000, null=True, blank=True)
    direct_download_url = models.URLField(max_length=3000)
    duration = models.IntegerField(null=True, blank=True)
    resolution = models.IntegerField(null=True, blank=True, default=720, help_text="Video resolution in pixels")  # ADD THIS FIELD
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['url_hash', 'resolution']),  # Add composite index for faster queries
        ]

    def __str__(self):
        return f"Cached: {self.title or self.original_url[:30]} - {self.resolution}p"