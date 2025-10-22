# Directory Monitoring for Automatic Media Import

MediaCMS can automatically monitor directories and import new media files as they are added. This feature is useful for workflows where media files are added to a NAS or file server through various means (upload, sync, recording, etc.) and you want them automatically available in MediaCMS.

## Features

- **Automatic Import**: Watches specified directories and automatically imports new media files
- **Deduplication**: Uses MD5 hashing to avoid importing the same file multiple times
- **Debouncing**: Waits for files to finish copying/writing before importing
- **File Filtering**: Optionally filter by file extensions
- **Recursive Monitoring**: Can monitor subdirectories
- **State Control**: Set default visibility state for imported media
- **Post-Import Actions**: Optionally move files after import to avoid re-processing
- **Multi-Directory Support**: Monitor multiple directories simultaneously

## Installation

The directory monitoring feature requires the `watchdog` Python package, which is included in the requirements.

To install or update dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

All configuration is done in `cms/settings.py` or `cms/local_settings.py`. Add or modify the following settings:

### Required Settings

```python
# List of directories to monitor
# Must be absolute paths
MEDIA_WATCH_DIRS = [
    '/path/to/media/folder1',
    '/path/to/media/folder2',
]

# Username of the account to assign imported media to
# This user must exist in the database
MEDIA_WATCH_USER = "admin"
```

### Optional Settings

```python
# Time in seconds to wait after a file is created/modified before importing
# This helps ensure the file has finished copying/writing
# Default: 5
MEDIA_WATCH_DEBOUNCE_SECONDS = 5

# File extensions to monitor (empty list means all supported media types)
# Extensions must include the dot
# Default: [] (all files)
MEDIA_WATCH_EXTENSIONS = ['.mp4', '.mkv', '.avi', '.mp3', '.jpg', '.png']

# Whether to monitor subdirectories recursively
# Default: True
MEDIA_WATCH_RECURSIVE = True

# Default state for auto-imported media
# Options: 'public', 'private', 'unlisted'
# Default: 'public'
MEDIA_WATCH_DEFAULT_STATE = 'public'

# Whether to move files after successful import
# This prevents re-importing the same file if the service restarts
# Default: False
MEDIA_WATCH_MOVE_AFTER_IMPORT = False

# Directory to move processed files to
# Only used if MEDIA_WATCH_MOVE_AFTER_IMPORT is True
# Imported files go to: {MEDIA_WATCH_PROCESSED_DIR}/imported/
# Duplicate files go to: {MEDIA_WATCH_PROCESSED_DIR}/duplicates/
# Default: ""
MEDIA_WATCH_PROCESSED_DIR = "/path/to/processed/files"
```

## Usage

### Testing Configuration

Before starting the monitor, you can test your configuration:

```bash
python manage.py monitor_directories --test
```

This will validate:
- All configured directories exist and are readable
- The specified user exists
- The processed directory is writable (if configured)

### Starting the Monitor

To start monitoring directories:

```bash
python manage.py monitor_directories
```

The monitor will:
1. Display the current configuration
2. Start watching all configured directories
3. Log all activity to the console and log files
4. Continue running until interrupted (Ctrl+C)

### Running as a Service

For production use, you should run the monitor as a system service.

#### Using systemd (recommended for Linux)

Create a systemd service file `/etc/systemd/system/mediacms-monitor.service`:

```ini
[Unit]
Description=MediaCMS Directory Monitor
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=mediacms
Group=mediacms
WorkingDirectory=/path/to/mediacms
Environment="PYTHONUNBUFFERED=1"
ExecStart=/path/to/mediacms/venv/bin/python manage.py monitor_directories
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable mediacms-monitor
sudo systemctl start mediacms-monitor
```

Check status:

```bash
sudo systemctl status mediacms-monitor
```

View logs:

```bash
sudo journalctl -u mediacms-monitor -f
```

#### Using Docker

If running MediaCMS in Docker, add a new service to your `docker-compose.yml`:

```yaml
services:
  monitor:
    image: mediacms/mediacms:latest
    volumes:
      - ./config:/home/mediacms.io/mediacms/cms/local_settings.py
      - ./media_files:/home/mediacms.io/mediacms/media_files
      - /path/to/watch/directory:/media/watch:ro  # Mount directories to watch
    command: python manage.py monitor_directories
    depends_on:
      - postgres
      - redis
    restart: unless-stopped
```

## How It Works

1. **File Detection**: The monitor uses the `watchdog` library to receive file system events when files are created or modified in watched directories.

2. **Debouncing**: When a file event is detected, the file is added to a pending queue with a timestamp. The monitor waits for the configured debounce period (default 5 seconds) to ensure the file has finished being written.

3. **Validation**: Before importing, the monitor checks:
   - File still exists and is readable
   - File is not empty
   - File size doesn't exceed `UPLOAD_MAX_SIZE`
   - File extension matches the filter (if configured)

4. **Deduplication**: The monitor calculates the MD5 hash of the file and checks if a media item with the same hash already exists in the database.

5. **Import**: If all checks pass, a new `Media` object is created with:
   - The file attached as `media_file`
   - Title extracted from filename
   - Auto-generated description with import timestamp
   - Assignment to the configured user
   - The configured default state

6. **Post-Processing**: MediaCMS's normal post-save processing takes over:
   - Media type detection (video, audio, image, pdf)
   - Thumbnail generation
   - Video transcoding (if enabled)
   - Metadata extraction

7. **Post-Import Actions**: If configured, the original file is moved to the processed directory to avoid re-importing.

## Logging

The monitor logs all activity using Python's `logging` module. Logs include:

- Service startup/shutdown
- Configuration details
- File detection events
- Import success/failure
- Deduplication hits
- Errors and warnings

Log level can be configured in Django's `LOGGING` settings.

## Example Workflow

### Scenario: NAS Auto-Import

You have a NAS at `/mnt/nas/incoming` where various tools and users upload media files. You want all files automatically imported to MediaCMS.

**Configuration:**

```python
# In cms/local_settings.py

MEDIA_WATCH_DIRS = ['/mnt/nas/incoming']
MEDIA_WATCH_USER = "nas-import"
MEDIA_WATCH_DEBOUNCE_SECONDS = 10  # Longer wait for network transfers
MEDIA_WATCH_MOVE_AFTER_IMPORT = True
MEDIA_WATCH_PROCESSED_DIR = "/mnt/nas/processed"
MEDIA_WATCH_DEFAULT_STATE = "public"
```

**Steps:**

1. Create the import user:
   ```bash
   python manage.py shell
   >>> from users.models import User
   >>> User.objects.create_user('nas-import', 'nas@example.com', 'password')
   ```

2. Test configuration:
   ```bash
   python manage.py monitor_directories --test
   ```

3. Start monitoring (as systemd service):
   ```bash
   sudo systemctl start mediacms-monitor
   ```

4. Drop files into `/mnt/nas/incoming`

5. Files are automatically imported and moved to `/mnt/nas/processed/imported/`

6. Update media details in MediaCMS UI as needed

## Troubleshooting

### Monitor won't start

- Check that all directories in `MEDIA_WATCH_DIRS` exist and are readable
- Verify the user specified in `MEDIA_WATCH_USER` exists
- Run with `--test` flag to validate configuration

### Files not being imported

- Check file extensions match `MEDIA_WATCH_EXTENSIONS` (if configured)
- Verify files are not empty or too large
- Check logs for error messages
- Ensure files are not being modified continuously (wait for debounce period)

### Duplicate imports

- The monitor uses MD5 hashing to detect duplicates
- If the same file content exists, it won't be imported again
- If you want to re-import, delete the existing media item first
- Enable `MEDIA_WATCH_MOVE_AFTER_IMPORT` to move files after import

### Permission errors

- Ensure the user running the monitor has read access to watched directories
- Ensure write access to `MEDIA_WATCH_PROCESSED_DIR` (if using move feature)
- Ensure write access to `MEDIA_ROOT` for media file storage

### Files imported with wrong user

- Check `MEDIA_WATCH_USER` setting matches the desired username
- Restart the monitor after changing settings

## Performance Considerations

- **Large Files**: The monitor calculates MD5 hash of each file. For very large files (>1GB), this may take time.
- **Many Files**: If monitoring directories with thousands of existing files, only new files will be imported. Existing files are ignored.
- **Network Directories**: For NFS/SMB mounts, increase `MEDIA_WATCH_DEBOUNCE_SECONDS` to account for network delays.
- **Resource Usage**: Each observer runs in a separate thread. Monitor memory usage if watching many directories.

## Security Notes

- Only import files from trusted sources
- Watched directories should not be world-writable
- Consider using `MEDIA_WATCH_DEFAULT_STATE = "private"` and manually review before making public
- The configured user will own all imported media
- Use `MEDIA_WATCH_EXTENSIONS` to restrict file types

## Limitations

- Only monitors file creation and modification events
- Does not retroactively import existing files (only new files after monitor starts)
- Moved/renamed files within watched directory will be treated as new files
- Symbolic links behavior depends on the operating system

## Future Enhancements

Possible future improvements:
- Initial import of existing files on first run
- Configurable import policies per directory
- Webhook notifications on import
- Batch import statistics/reporting
- Integration with external metadata sources
