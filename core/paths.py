"""
Shared filesystem locations for user data.

Runtime data belongs outside the project folder so packaged builds and source
checkouts do not mix user state with application code.
"""

from __future__ import annotations

import os
import shutil

from core.native import app_support_dir

APP_NAME = "Whisperer"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_app_data_dir() -> str:
    """Return the per-user app data directory, creating it if needed."""
    return app_support_dir(APP_NAME)


def get_data_dir() -> str:
    """Return the app data directory used for databases and generated files."""
    path = os.path.join(get_app_data_dir(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def get_dictionary_db_path() -> str:
    """
    Return the user dictionary database path.

    If an older repo-local database exists and the app-data database does not,
    copy it forward once so existing learned terms survive the migration.
    """
    db_path = os.path.join(get_data_dir(), "dictionary.db")
    legacy_path = os.path.join(PROJECT_ROOT, "data", "dictionary.db")
    if not os.path.exists(db_path) and os.path.exists(legacy_path):
        shutil.copy2(legacy_path, db_path)
    return db_path


def database_path() -> str:
    """Return the main application database path."""
    return os.path.join(get_app_data_dir(), "whisperer.db")
