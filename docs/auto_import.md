# Automatic media imports

MediaCMS can monitor folders on the filesystem and automatically ingest any
media file that appears in them. The watcher runs through the
``watch_media_directories`` management command and relies on the
``MEDIA_AUTO_IMPORT_DIRECTORIES`` configuration.

## Configure watched folders

Add entries to ``MEDIA_AUTO_IMPORT_DIRECTORIES`` inside your settings module
(``cms/settings_local.py`` in most deployments). Each entry is a dictionary with
the directory path and additional metadata used during the import:

```python
MEDIA_AUTO_IMPORT_DIRECTORIES = [
    {
        "name": "family-archive",            # Optional label shown in logs
        "path": "/srv/nas/media/family",    # Folder to monitor (required)
        "user": "admin@example.com",        # Username or email of the owner (required)
        "recursive": True,                   # Scan sub-folders as well (default: True)
        "extensions": ["mp4", "mov", "jpg"],# Only import these file types (optional)
        "state": "private",                  # Force media state (optional)
        "allow_download": True,              # Override download flag (optional)
        "is_reviewed": False,                # Override review state (optional)
        "channel": "default",               # Channel friendly token or title (optional)
        "categories": ["home-videos"],      # Category UID or title values (optional)
        "tags": ["family", "archive"],      # Tags to assign (optional)
        "delete_after_import": False,        # Remove source file after ingest (optional)
        "description": "Imported from NAS"  # Default description (optional)
    },
]
```

When ``extensions`` is omitted, all files are eligible. Extension values can be
specified with or without the leading dot.

## Running the watcher

Launch the watcher with:

```bash
python manage.py watch_media_directories
```

By default, the command scans the configured folders every 30 seconds. Useful
options include:

* ``--once`` – Perform a single scan and exit. Handy for cron jobs.
* ``--interval N`` – Wait ``N`` seconds between scans (default: ``30``).
* ``--dry-run`` – Print the files that would be imported without saving them.
* ``--force`` – Re-import files even if they were previously processed.

Imported files are tracked through the ``MediaAutoImportRecord`` model, so a
file is only imported once unless ``--force`` is supplied. When
``delete_after_import`` is set, the original file is removed after the media has
been created inside MediaCMS.

