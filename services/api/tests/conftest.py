from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

# The tests must never share storage with a running server. An earlier version
# pointed GREENVIDEO_STORAGE_DIR at the live "./storage" and then rmtree'd its
# uploads directory, which deleted real user uploads and finished task outputs
# every time pytest ran.
TEST_ROOT = Path(__file__).resolve().parent / "_test_storage"

# Four slashes: "sqlite:///" + an absolute POSIX path.
os.environ["GREENVIDEO_DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test_greenvideo.db').as_posix()}"
os.environ["GREENVIDEO_STORAGE_DIR"] = str(TEST_ROOT)
os.environ["GREENVIDEO_IOPAINT_ENABLED"] = "false"

from app.db import database_path, engine  # noqa: E402

db_file = database_path()
if db_file.exists():
    engine.dispose()
    db_file.unlink()


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    engine.dispose()
    # Only the isolated test tree is removed, never the live storage directory.
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
