"""Settings module tailored for running the automated test-suite.

This module imports everything from the default settings and overrides the
database configuration so the tests can run without a PostgreSQL server.
"""

import os

from .settings import *  # noqa: F401,F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "test.sqlite3"),
    }
}

# Speed up tests by using a lightweight password hasher.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

