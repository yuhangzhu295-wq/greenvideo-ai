from __future__ import annotations

import os
import shutil

import pytest

os.environ["GREENVIDEO_DATABASE_URL"] = "sqlite:///./storage/test_greenvideo.db"
os.environ["GREENVIDEO_STORAGE_DIR"] = "./storage"

from app.db import database_path, engine

db_file = database_path()
if db_file.exists():
    engine.dispose()
    db_file.unlink()


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield
    engine.dispose()
    if db_file.exists():
        try:
            db_file.unlink()
        except OSError:
            pass
    test_files = os.path.join("storage", "files")
    if os.path.isdir(test_files):
        for name in os.listdir(test_files):
            if name.startswith(("task_", "new_file_id")):
                path = os.path.join(test_files, name)
                if os.path.isfile(path):
                    os.remove(path)
    test_uploads = os.path.join("storage", "uploads")
    if os.path.isdir(test_uploads):
        shutil.rmtree(test_uploads, ignore_errors=True)
