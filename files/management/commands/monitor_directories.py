"""
Django management command to start directory monitoring for automatic media import.

Usage:
    python manage.py monitor_directories
"""

from django.core.management.base import BaseCommand

from files.directory_monitor import DirectoryMonitor


class Command(BaseCommand):
    help = 'Monitor directories for automatic media import'

    def add_arguments(self, parser):
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test configuration without starting the monitor',
        )

    def handle(self, *args, **options):
        """Handle the command execution."""
        from django.conf import settings

        if options['test']:
            # Test mode - just validate configuration
            self.stdout.write(self.style.WARNING('Running in test mode - validating configuration...'))
            self._test_configuration()
            return

        self.stdout.write(self.style.SUCCESS('Starting directory monitoring service...'))
        self.stdout.write('')

        # Display configuration
        self._display_configuration()

        # Create and start monitor
        monitor = DirectoryMonitor()

        if not monitor.start():
            self.stdout.write(self.style.ERROR('Failed to start directory monitoring'))
            self.stdout.write(self.style.ERROR('Please check the configuration and logs'))
            return

        self.stdout.write(self.style.SUCCESS('Directory monitoring is running'))
        self.stdout.write('Press Ctrl+C to stop...')
        self.stdout.write('')

        # Wait for interrupt
        try:
            monitor.wait()
        except KeyboardInterrupt:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Stopping...'))
            monitor.stop()
            self.stdout.write(self.style.SUCCESS('Directory monitoring stopped'))

    def _display_configuration(self):
        """Display current configuration."""
        from django.conf import settings

        watch_dirs = getattr(settings, 'MEDIA_WATCH_DIRS', [])
        watch_user = getattr(settings, 'MEDIA_WATCH_USER', '')
        debounce_seconds = getattr(settings, 'MEDIA_WATCH_DEBOUNCE_SECONDS', 5)
        extensions = getattr(settings, 'MEDIA_WATCH_EXTENSIONS', [])
        recursive = getattr(settings, 'MEDIA_WATCH_RECURSIVE', True)
        default_state = getattr(settings, 'MEDIA_WATCH_DEFAULT_STATE', 'public')
        move_after_import = getattr(settings, 'MEDIA_WATCH_MOVE_AFTER_IMPORT', False)
        processed_dir = getattr(settings, 'MEDIA_WATCH_PROCESSED_DIR', '')

        self.stdout.write(self.style.MIGRATE_HEADING('Configuration:'))
        self.stdout.write(f'  Monitored directories: {len(watch_dirs)}')
        for i, dir_path in enumerate(watch_dirs, 1):
            self.stdout.write(f'    {i}. {dir_path}')

        self.stdout.write(f'  User: {watch_user}')
        self.stdout.write(f'  Debounce delay: {debounce_seconds} seconds')
        self.stdout.write(f'  Recursive monitoring: {recursive}')
        self.stdout.write(f'  Default state: {default_state}')

        if extensions:
            self.stdout.write(f'  File extensions: {", ".join(extensions)}')
        else:
            self.stdout.write('  File extensions: All media types')

        if move_after_import:
            self.stdout.write(f'  Move after import: Yes')
            self.stdout.write(f'  Processed files directory: {processed_dir}')
        else:
            self.stdout.write(f'  Move after import: No')

        self.stdout.write('')

    def _test_configuration(self):
        """Test and validate configuration."""
        import os
        from django.conf import settings
        from django.contrib.auth import get_user_model

        User = get_user_model()

        watch_dirs = getattr(settings, 'MEDIA_WATCH_DIRS', [])
        watch_user = getattr(settings, 'MEDIA_WATCH_USER', '')
        move_after_import = getattr(settings, 'MEDIA_WATCH_MOVE_AFTER_IMPORT', False)
        processed_dir = getattr(settings, 'MEDIA_WATCH_PROCESSED_DIR', '')

        errors = []
        warnings = []

        # Check watch directories
        if not watch_dirs:
            errors.append('MEDIA_WATCH_DIRS is not configured or empty')
        else:
            self.stdout.write(f'Found {len(watch_dirs)} configured director(y/ies):')
            for watch_dir in watch_dirs:
                if not os.path.exists(watch_dir):
                    warnings.append(f'Directory does not exist: {watch_dir}')
                elif not os.path.isdir(watch_dir):
                    warnings.append(f'Path is not a directory: {watch_dir}')
                elif not os.access(watch_dir, os.R_OK):
                    errors.append(f'Directory is not readable: {watch_dir}')
                else:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ {watch_dir}'))

        # Check user
        if not watch_user:
            errors.append('MEDIA_WATCH_USER is not configured')
        else:
            try:
                user = User.objects.get(username=watch_user)
                self.stdout.write(self.style.SUCCESS(f'✓ User "{watch_user}" exists (ID: {user.id})'))
            except User.DoesNotExist:
                errors.append(f'User "{watch_user}" does not exist')

        # Check processed directory if move_after_import is enabled
        if move_after_import:
            if not processed_dir:
                errors.append('MEDIA_WATCH_MOVE_AFTER_IMPORT is enabled but MEDIA_WATCH_PROCESSED_DIR is not set')
            else:
                if os.path.exists(processed_dir):
                    if not os.path.isdir(processed_dir):
                        errors.append(f'MEDIA_WATCH_PROCESSED_DIR is not a directory: {processed_dir}')
                    elif not os.access(processed_dir, os.W_OK):
                        errors.append(f'MEDIA_WATCH_PROCESSED_DIR is not writable: {processed_dir}')
                    else:
                        self.stdout.write(self.style.SUCCESS(f'✓ Processed directory is writable: {processed_dir}'))
                else:
                    self.stdout.write(self.style.WARNING(f'! Processed directory will be created: {processed_dir}'))

        # Display warnings
        if warnings:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Warnings:'))
            for warning in warnings:
                self.stdout.write(self.style.WARNING(f'  ! {warning}'))

        # Display errors
        if errors:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('Errors:'))
            for error in errors:
                self.stdout.write(self.style.ERROR(f'  ✗ {error}'))
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('Configuration is invalid. Please fix the errors above.'))
        else:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('✓ Configuration is valid!'))

        # Display full configuration
        self.stdout.write('')
        self._display_configuration()
