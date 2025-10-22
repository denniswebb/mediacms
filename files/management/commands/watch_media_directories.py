import logging
import os
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from files import helpers
from files.models import Category, Media, MediaAutoImportRecord, Tag
from users.models import Channel, User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Watch configured directories and import new media automatically."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single scan instead of continuously watching.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Seconds to wait between scans when running continuously.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the files that would be imported without saving anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-import files even if they were processed previously.",
        )

    def handle(self, *args, **options):
        configs = getattr(settings, "MEDIA_AUTO_IMPORT_DIRECTORIES", []) or []
        if not isinstance(configs, Sequence):
            raise CommandError("MEDIA_AUTO_IMPORT_DIRECTORIES must be a list or tuple of dictionaries")
        if not configs:
            self.stdout.write(self.style.WARNING("No watch directories configured."))
            return

        try:
            while True:
                for config in configs:
                    try:
                        self.process_config(config, options)
                    except CommandError as exc:
                        raise
                    except Exception as exc:  # pragma: no cover - safety net
                        logger.exception("Failed to process watch configuration %s", config)
                        self.stderr.write(self.style.ERROR(str(exc)))
                if options["once"]:
                    break
                time.sleep(max(options["interval"], 1))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Watcher interrupted by user."))

    # ------------------------------------------------------------------
    # Core functionality
    # ------------------------------------------------------------------

    def process_config(self, config, options):
        if not isinstance(config, dict):
            raise CommandError("Each watcher configuration must be a dictionary")
        if "path" not in config:
            raise CommandError("Watcher configuration is missing the 'path' key")

        path = Path(config["path"]).expanduser()
        if not path.exists() or not path.is_dir():
            raise CommandError(f"Watch directory does not exist: {path}")

        config_name = config.get("name") or str(path)
        recursive = config.get("recursive", True)
        extensions = self._normalise_extensions(config.get("extensions") or [])
        delete_after = bool(config.get("delete_after_import"))

        user = self._resolve_user(config)
        channel = self._resolve_channel(config, user)
        categories = self._resolve_categories(config, user)
        tags = self._resolve_tags(config, user)

        for file_path in self._iter_files(path, recursive):
            if extensions and file_path.suffix.lower().lstrip(".") not in extensions:
                continue

            source_path = str(file_path.resolve())
            record, created = MediaAutoImportRecord.objects.get_or_create(
                source_path=source_path,
                defaults={"config_name": config_name},
            )

            if not created:
                # Update last seen timestamp
                updates = ["last_seen"]
                if record.config_name != config_name:
                    record.config_name = config_name
                    updates.append("config_name")
                record.save(update_fields=updates)

            if record.is_imported and not options["force"]:
                continue

            if options["dry_run"]:
                self.stdout.write(f"[dry-run] Would import {source_path}")
                continue

            try:
                media = self._import_file(
                    file_path=file_path,
                    user=user,
                    channel=channel,
                    categories=categories,
                    tags=tags,
                    state=config.get("state"),
                    allow_download=config.get("allow_download"),
                    is_reviewed=config.get("is_reviewed"),
                    description=config.get("description"),
                )
            except Exception as exc:  # pragma: no cover - safety net
                logger.exception("Unable to import %s", source_path)
                self.stderr.write(self.style.ERROR(f"Failed to import {source_path}: {exc}"))
                continue

            record.media = media
            record.imported_at = timezone.now()
            record.md5sum = media.md5sum
            record.config_name = config_name
            record.save(update_fields=["media", "imported_at", "md5sum", "config_name", "last_seen"])

            self.stdout.write(self.style.SUCCESS(f"Imported {source_path} as {media.friendly_token}"))

            if delete_after:
                helpers.rm_file(source_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _iter_files(self, base_path: Path, recursive: bool) -> Iterable[Path]:
        if recursive:
            iterator = base_path.rglob("*")
        else:
            iterator = base_path.glob("*")
        for item in iterator:
            if item.is_file():
                yield item

    def _normalise_extensions(self, extensions: Sequence[str]) -> set:
        return {ext.lower().lstrip(".") for ext in extensions if isinstance(ext, str)}

    def _resolve_user(self, config) -> User:
        identifier = config.get("user")
        if not identifier:
            raise CommandError("Watcher configuration requires a 'user' to assign media to")
        user = User.objects.filter(Q(username=identifier) | Q(email=identifier)).first()
        if not user:
            raise CommandError(f"Cannot find user with username/email '{identifier}'")
        return user

    def _resolve_channel(self, config, user: User) -> Optional[Channel]:
        identifier = config.get("channel")
        if not identifier:
            return None
        channel = Channel.objects.filter(Q(friendly_token=identifier) | Q(title=identifier, user=user)).first()
        if not channel:
            raise CommandError(f"Cannot find channel '{identifier}' for user {user}")
        return channel

    def _resolve_categories(self, config, user: User) -> List[Category]:
        values = config.get("categories") or []
        if not isinstance(values, (list, tuple, set)):
            raise CommandError("'categories' configuration must be a list")
        categories: List[Category] = []
        for value in values:
            if not value:
                continue
            category = Category.objects.filter(Q(uid=value) | Q(title=value)).first()
            if not category:
                raise CommandError(f"Cannot find category '{value}'")
            categories.append(category)
        return categories

    def _resolve_tags(self, config, user: User) -> List[Tag]:
        values = config.get("tags") or []
        if not isinstance(values, (list, tuple, set)):
            raise CommandError("'tags' configuration must be a list")
        tags: List[Tag] = []
        for value in values:
            if not value:
                continue
            tag, _ = Tag.objects.get_or_create(title=value, defaults={"user": user})
            tags.append(tag)
        return tags

    @transaction.atomic
    def _import_file(
        self,
        *,
        file_path: Path,
        user: User,
        channel: Optional[Channel],
        categories: Sequence[Category],
        tags: Sequence[Tag],
        state: Optional[str],
        allow_download: Optional[bool],
        is_reviewed: Optional[bool],
        description: Optional[str],
    ) -> Media:
        kwargs = {
            "user": user,
        }
        if state:
            kwargs["state"] = state
        if allow_download is not None:
            kwargs["allow_download"] = bool(allow_download)
        if is_reviewed is not None:
            kwargs["is_reviewed"] = bool(is_reviewed)
        if description:
            kwargs["description"] = description

        with open(file_path, "rb") as fh:
            django_file = File(fh, name=os.path.basename(file_path))
            media = Media.objects.create(media_file=django_file, **kwargs)

        if channel:
            media.channel = channel
            media.save(update_fields=["channel"])

        if categories:
            media.category.add(*categories)

        if tags:
            media.tags.add(*tags)

        # refresh to pick up data from media_init (md5sum, etc.)
        media.refresh_from_db()
        return media

