from django.conf import settings
from django.db import models


class MediaAutoImportRecord(models.Model):
    """Track files that were imported from watched folders."""

    source_path = models.CharField(
        max_length=2048,
        unique=True,
        help_text="Absolute path to the original file on disk.",
    )
    config_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Identifier of the watch configuration that imported the file.",
    )
    media = models.ForeignKey(
        "files.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auto_import_records",
    )
    md5sum = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Checksum captured after the media file was imported.",
    )
    imported_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the media was imported into MediaCMS.",
    )
    last_seen = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp of the last time the watcher encountered the file.",
    )

    class Meta:
        verbose_name = "Watched media import"
        verbose_name_plural = "Watched media imports"
        indexes = [
            models.Index(fields=["config_name"]),
            models.Index(fields=["imported_at"]),
        ]

    def __str__(self):
        owner = self.media.user.username if self.media else "unassigned"
        return f"{self.source_path} ({owner})"

    @property
    def is_imported(self):
        """Return True when the file was already imported."""

        return bool(self.media_id)

