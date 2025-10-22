import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from files.models import Media, MediaAutoImportRecord
from users.models import User


class WatchMediaDirectoriesCommandTest(TestCase):
    def setUp(self):
        self.watch_directory = tempfile.mkdtemp()
        self.media_root = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            username="auto-importer",
            email="auto-importer@example.com",
            password="password123",
        )
        self.user.name = "Auto Importer"
        self.user.save()

    def tearDown(self):
        shutil.rmtree(self.watch_directory, ignore_errors=True)
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _create_sample_image(self, filename: str) -> Path:
        path = Path(self.watch_directory) / filename
        from PIL import Image

        image = Image.new("RGB", (32, 32), color="red")
        image.save(path, format="JPEG")
        return path

    def test_imports_new_media_and_records_source(self):
        sample = self._create_sample_image("example.jpg")

        config = [
            {
                "path": self.watch_directory,
                "user": self.user.username,
                "recursive": False,
            }
        ]

        with override_settings(MEDIA_ROOT=self.media_root, MEDIA_AUTO_IMPORT_DIRECTORIES=config):
            call_command("watch_media_directories", "--once")
            self.assertEqual(Media.objects.count(), 1)

            media = Media.objects.first()
            self.assertEqual(media.user, self.user)

            record = MediaAutoImportRecord.objects.get(source_path=str(sample.resolve()))
            self.assertEqual(record.media, media)
            self.assertIsNotNone(record.imported_at)

            # Second execution should not create duplicates
            call_command("watch_media_directories", "--once")
            self.assertEqual(Media.objects.count(), 1)
            record.refresh_from_db()
            self.assertEqual(record.media, media)

