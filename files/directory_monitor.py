"""
Directory monitoring service for automatic media import.

This module provides functionality to watch specified directories for new media files
and automatically import them into MediaCMS.
"""

import hashlib
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from threading import Thread, Lock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from files.models import Media

User = get_user_model()
logger = logging.getLogger(__name__)


class MediaFileHandler(FileSystemEventHandler):
    """
    Handles file system events for media files.

    Debounces file creation/modification events to ensure files are fully written
    before attempting to import them.
    """

    def __init__(self, user, debounce_seconds=5, extensions=None, default_state='public',
                 move_after_import=False, processed_dir=None):
        """
        Initialize the media file handler.

        Args:
            user: Django User object to assign imported media to
            debounce_seconds: Time to wait after file event before processing
            extensions: List of file extensions to monitor (e.g., ['.mp4', '.jpg'])
                       If None or empty, all file types are monitored
            default_state: Default state for imported media ('public', 'private', 'unlisted')
            move_after_import: Whether to move files after successful import
            processed_dir: Directory to move processed files to
        """
        super().__init__()
        self.user = user
        self.debounce_seconds = debounce_seconds
        self.extensions = [ext.lower() for ext in extensions] if extensions else []
        self.default_state = default_state
        self.move_after_import = move_after_import
        self.processed_dir = processed_dir

        # Track pending files with their event times
        self.pending_files = {}
        self.lock = Lock()

        # Start debounce worker thread
        self.running = True
        self.worker_thread = Thread(target=self._process_pending_files, daemon=True)
        self.worker_thread.start()

        logger.info(f"MediaFileHandler initialized for user: {user.username}")
        if self.extensions:
            logger.info(f"Monitoring extensions: {', '.join(self.extensions)}")
        else:
            logger.info("Monitoring all file extensions")

    def stop(self):
        """Stop the handler and worker thread."""
        self.running = False
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)

    def _should_process_file(self, file_path):
        """
        Check if a file should be processed based on extension filter.

        Args:
            file_path: Path to the file

        Returns:
            bool: True if file should be processed
        """
        # Ignore hidden files and temp files
        filename = os.path.basename(file_path)
        if filename.startswith('.') or filename.endswith('.tmp') or filename.endswith('.part'):
            return False

        # If no extensions specified, process all files
        if not self.extensions:
            return True

        # Check if file extension matches filter
        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in self.extensions

    def on_created(self, event):
        """Handle file creation events."""
        if event.is_directory:
            return

        file_path = event.src_path

        if not self._should_process_file(file_path):
            return

        logger.info(f"New file detected: {file_path}")

        with self.lock:
            self.pending_files[file_path] = time.time()

    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return

        file_path = event.src_path

        if not self._should_process_file(file_path):
            return

        # Update the timestamp for this file
        with self.lock:
            if file_path not in self.pending_files:
                logger.debug(f"File modified: {file_path}")
            self.pending_files[file_path] = time.time()

    def _process_pending_files(self):
        """
        Worker thread that processes pending files after debounce period.
        """
        while self.running:
            try:
                current_time = time.time()
                files_to_process = []

                with self.lock:
                    for file_path, event_time in list(self.pending_files.items()):
                        # Check if enough time has passed since last event
                        if current_time - event_time >= self.debounce_seconds:
                            files_to_process.append(file_path)
                            del self.pending_files[file_path]

                # Process files outside of lock
                for file_path in files_to_process:
                    self._import_media_file(file_path)

                # Sleep to avoid busy waiting
                time.sleep(1)

            except Exception as e:
                logger.error(f"Error in pending files worker: {e}", exc_info=True)

    def _calculate_md5(self, file_path):
        """
        Calculate MD5 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            str: MD5 hash of the file
        """
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                # Read in chunks to handle large files
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating MD5 for {file_path}: {e}")
            return None

    def _import_media_file(self, file_path):
        """
        Import a media file into MediaCMS.

        Args:
            file_path: Path to the media file to import
        """
        try:
            # Verify file still exists and is readable
            if not os.path.exists(file_path):
                logger.warning(f"File no longer exists: {file_path}")
                return

            if not os.path.isfile(file_path):
                logger.warning(f"Path is not a file: {file_path}")
                return

            # Check file size
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"File is empty: {file_path}")
                return

            # Check if file size exceeds maximum
            max_size = getattr(settings, 'UPLOAD_MAX_SIZE', 800 * 1024 * 1000 * 5)
            if file_size > max_size:
                logger.warning(f"File too large ({file_size} bytes): {file_path}")
                return

            # Calculate MD5 for deduplication
            md5sum = self._calculate_md5(file_path)
            if not md5sum:
                logger.error(f"Could not calculate MD5, skipping: {file_path}")
                return

            # Check if media with this MD5 already exists
            existing_media = Media.objects.filter(md5sum=md5sum).first()
            if existing_media:
                logger.info(f"File already imported (MD5: {md5sum}): {file_path}")
                logger.info(f"Existing media: {existing_media.title} ({existing_media.friendly_token})")

                # Optionally move the duplicate file
                if self.move_after_import and self.processed_dir:
                    self._move_processed_file(file_path, duplicate=True)

                return

            # Extract filename for title
            filename = os.path.basename(file_path)
            title = os.path.splitext(filename)[0]

            # Create Media object
            logger.info(f"Importing media file: {file_path}")

            with open(file_path, 'rb') as f:
                django_file = File(f, name=filename)

                media = Media(
                    user=self.user,
                    title=title,
                    description=f"Auto-imported from directory on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    md5sum=md5sum,
                )

                # Set state if different from default
                if self.default_state != 'public':
                    media.state = self.default_state

                # Save the media file
                media.media_file.save(filename, django_file, save=False)

                # Save the media object (this will trigger post_save signal and media_init)
                media.save()

            logger.info(f"Successfully imported: {title} ({media.friendly_token})")
            logger.info(f"Media URL: /media/{media.friendly_token}")

            # Move file after successful import if configured
            if self.move_after_import and self.processed_dir:
                self._move_processed_file(file_path)

        except Exception as e:
            logger.error(f"Error importing file {file_path}: {e}", exc_info=True)

    def _move_processed_file(self, file_path, duplicate=False):
        """
        Move a processed file to the processed directory.

        Args:
            file_path: Path to the file to move
            duplicate: Whether this is a duplicate file
        """
        try:
            if not self.processed_dir:
                return

            # Create processed directory if it doesn't exist
            processed_dir = Path(self.processed_dir)

            # Create subdirectories for duplicates and imported files
            if duplicate:
                target_dir = processed_dir / "duplicates"
            else:
                target_dir = processed_dir / "imported"

            target_dir.mkdir(parents=True, exist_ok=True)

            # Get filename and create target path
            filename = os.path.basename(file_path)
            target_path = target_dir / filename

            # If file already exists in target, add timestamp to filename
            if target_path.exists():
                name, ext = os.path.splitext(filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                target_path = target_dir / f"{name}_{timestamp}{ext}"

            # Move the file
            shutil.move(file_path, str(target_path))
            logger.info(f"Moved processed file to: {target_path}")

        except Exception as e:
            logger.error(f"Error moving processed file {file_path}: {e}", exc_info=True)


class DirectoryMonitor:
    """
    Main directory monitoring service.

    Manages watchdog observers for multiple directories.
    """

    def __init__(self):
        """Initialize the directory monitor."""
        self.observers = []
        self.handlers = []
        self.running = False

    def start(self):
        """
        Start monitoring configured directories.

        Returns:
            bool: True if monitoring started successfully, False otherwise
        """
        # Get configuration from settings
        watch_dirs = getattr(settings, 'MEDIA_WATCH_DIRS', [])
        watch_user = getattr(settings, 'MEDIA_WATCH_USER', '')
        debounce_seconds = getattr(settings, 'MEDIA_WATCH_DEBOUNCE_SECONDS', 5)
        extensions = getattr(settings, 'MEDIA_WATCH_EXTENSIONS', [])
        recursive = getattr(settings, 'MEDIA_WATCH_RECURSIVE', True)
        default_state = getattr(settings, 'MEDIA_WATCH_DEFAULT_STATE', 'public')
        move_after_import = getattr(settings, 'MEDIA_WATCH_MOVE_AFTER_IMPORT', False)
        processed_dir = getattr(settings, 'MEDIA_WATCH_PROCESSED_DIR', '')

        # Validate configuration
        if not watch_dirs:
            logger.error("MEDIA_WATCH_DIRS is not configured or empty")
            return False

        if not watch_user:
            logger.error("MEDIA_WATCH_USER is not configured")
            return False

        # Get user object
        try:
            user = User.objects.get(username=watch_user)
        except User.DoesNotExist:
            logger.error(f"User '{watch_user}' not found")
            return False

        # Validate directories
        valid_dirs = []
        for watch_dir in watch_dirs:
            if not os.path.exists(watch_dir):
                logger.warning(f"Directory does not exist: {watch_dir}")
                continue

            if not os.path.isdir(watch_dir):
                logger.warning(f"Path is not a directory: {watch_dir}")
                continue

            if not os.access(watch_dir, os.R_OK):
                logger.warning(f"Directory is not readable: {watch_dir}")
                continue

            valid_dirs.append(watch_dir)

        if not valid_dirs:
            logger.error("No valid directories to monitor")
            return False

        # Validate processed directory if move_after_import is enabled
        if move_after_import:
            if not processed_dir:
                logger.error("MEDIA_WATCH_MOVE_AFTER_IMPORT is enabled but MEDIA_WATCH_PROCESSED_DIR is not set")
                return False

            # Create processed directory if it doesn't exist
            try:
                Path(processed_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Cannot create processed directory {processed_dir}: {e}")
                return False

        # Create handler
        handler = MediaFileHandler(
            user=user,
            debounce_seconds=debounce_seconds,
            extensions=extensions,
            default_state=default_state,
            move_after_import=move_after_import,
            processed_dir=processed_dir if move_after_import else None
        )
        self.handlers.append(handler)

        # Create observers for each directory
        for watch_dir in valid_dirs:
            try:
                observer = Observer()
                observer.schedule(handler, watch_dir, recursive=recursive)
                observer.start()
                self.observers.append(observer)

                logger.info(f"Started monitoring directory: {watch_dir} (recursive={recursive})")

            except Exception as e:
                logger.error(f"Error starting observer for {watch_dir}: {e}", exc_info=True)

        if not self.observers:
            logger.error("Failed to start any observers")
            return False

        self.running = True
        logger.info(f"Directory monitoring started with {len(self.observers)} observer(s)")
        logger.info(f"Assigning imported media to user: {user.username}")
        logger.info(f"Default state for imported media: {default_state}")

        return True

    def stop(self):
        """Stop monitoring all directories."""
        logger.info("Stopping directory monitoring...")

        # Stop handlers
        for handler in self.handlers:
            handler.stop()

        # Stop observers
        for observer in self.observers:
            observer.stop()
            observer.join(timeout=5)

        self.observers.clear()
        self.handlers.clear()
        self.running = False

        logger.info("Directory monitoring stopped")

    def wait(self):
        """Wait for observers to finish (blocking)."""
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
            self.stop()
