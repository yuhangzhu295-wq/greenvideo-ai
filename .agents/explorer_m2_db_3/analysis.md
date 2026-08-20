# Analysis: Database Usage in the Test Suite & Refactoring Recommendations

## Summary
The test suite currently executes integration tests directly against the local, persistent development SQLite database file (`services/api/storage/greenvideo.db`). To ensure compatibility and clean execution with the new SQLModel/SQLAlchemy implementation, we recommend introducing a test database URL override (either via environment variable or dependency injection overrides) inside a pytest `conftest.py` file, enabling clean test isolation.

---

## 1. Current Database Connections and File Expectations in Tests

### Test Files Overview
There are two test files in `services/api/tests`:
1. **`test_security.py`**: Focuses on URL safety checks in the security service. It has **no database dependencies**.
2. **`test_api.py`**: Performs integration tests on the FastAPI routers using `fastapi.testclient.TestClient`. It is **heavily dependent on the database**.

### Database Interactions & File Creation
* **Database File Path**: The database location is configured in `app/config.py` (defaulting to `sqlite:///./storage/greenvideo.db`). When running pytest inside `services/api`, the tests expect and write to `./storage/greenvideo.db`.
* **Initialization and Seeding**: When `test_api.py` imports `app` from `app.main`, it triggers the application startup flow:
  1. `app.main.create_app()` runs.
  2. It calls `ensure_storage()` and `init_db()` from `app/db.py`.
  3. `init_db()` connects to the database file, creates tables if they do not exist, and runs `seed_demo_data()` to insert a demo user (`user_demo`) and 4 history items.
* **Test Methods Database Usage**:
  * `test_parse_demo_url()`: Sends a POST request to `/api/parse`, which calls `insert_parse_result()` to persist a video parse result in the `parse_results` table.
  * `test_download_task_and_status()`: Sends a POST request to `/api/tasks/download`, inserting a download task (`insert_task()`), updating it (`update_task()`), and inserting a new history item (`insert_history()`). It then does a GET request for the task state (`get_task()`).
  * `test_watermark_task_json()`: Sends a POST request to `/api/tasks/watermark`, inserting a watermark task (`insert_task()`).
  * `test_history_and_feedback()`:
    * Sends a GET request to `/api/history`, which calls `list_history()`. It asserts `len(history.json()["items"]) >= 4`. Since the demo data seeds 4 history items, this assertion passes but relies on the persistent state of the database (hence the `>= 4` check rather than exactly `== 4`).
    * Sends a POST request to `/api/feedback`, calling `insert_feedback()` to write to the `feedback` table.

### Current Shortcomings
* **Lack of Isolation**: Running tests modifies the developer's database file (`storage/greenvideo.db`) directly. Test runs are not isolated, so the database accumulates test records over time.
* **No Rollback**: Test data is never deleted or rolled back after a test runs, which can lead to flaky assertions if the schema or seeded content changes.

---

## 2. Refactoring Recommendations for SQLModel/SQLAlchemy

As we migrate from raw SQLite connection/execution helpers to SQLModel (which uses SQLAlchemy engines and sessions), we should introduce proper test isolation. Below are two proposed strategies for the implementation.

### Strategy A: Test Database File Override (Recommended & Most Compatible)
This approach overrides the database settings at the environment level before the application and database engines are initialized. It works regardless of whether the code uses direct function calls or FastAPI dependency injection.

1. **Create `services/api/tests/conftest.py`**:
   We configure pytest to override the `GREENVIDEO_DATABASE_URL` environment variable to point to a temporary test database file (e.g., `./storage/test_greenvideo.db`).

2. **Database Setup & Cleanup in Fixture**:
   The fixture creates tables, seeds the demo data, runs the tests, and deletes the test database file during teardown.

#### Code Draft for `conftest.py`:
```python
import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# 1. Override the database URL environment variable BEFORE importing app or db
os.environ["GREENVIDEO_DATABASE_URL"] = "sqlite:///./storage/test_greenvideo.db"
os.environ["GREENVIDEO_STORAGE_DIR"] = "./storage"

from app.main import create_app
from app.db import database_path

@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    # Allow the app's startup to create tables and seed demo data on the test DB
    yield
    
    # Teardown: Clean up the test database file after all tests finish
    db_file = database_path()
    if db_file.exists():
        try:
            db_file.unlink()
        except OSError:
            pass  # Handle file locking issues if connections are not fully closed
```

* **Why this works**: By overriding `GREENVIDEO_DATABASE_URL` before imports, `app.config.settings.database_url` is configured with `test_greenvideo.db`. The application startup automatically runs `init_db()` on the clean test database, seeding the required history items and users. The tests run normally, and the test database is cleaned up afterward.

---

### Strategy B: FastAPI Dependency Injection Override (Standard SQLModel Pattern)
If the routers are refactored to retrieve SQLModel sessions using FastAPI's dependency injection (e.g., `Depends(get_session)`), we can override the dependency in pytest.

1. **Session Dependency in App Code (`app/db.py`)**:
   ```python
   from sqlmodel import Session, create_engine
   
   engine = create_engine(settings.database_url)
   
   def get_session():
       with Session(engine) as session:
           yield session
   ```

2. **Router Dependency Injection (`app/routers/history.py`)**:
   ```python
   @router.get("/history", response_model=HistoryResponse)
   def get_history(session: Session = Depends(get_session)) -> HistoryResponse:
       # Query database using session...
   ```

3. **Fixture Override in `conftest.py`**:
   We can override the `get_session` dependency to use an in-memory SQLite database or a test database.

#### Code Draft for `conftest.py` (Dependency Override):
```python
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from app.main import app
from app.db import get_session

# Test database engine
TEST_DATABASE_URL = "sqlite:///:memory:"
# Note: For in-memory sqlite, we must use a StaticPool to share connection across sessions
from sqlalchemy.pool import StaticPool
test_engine = create_engine(
    TEST_DATABASE_URL, 
    connect_args={"check_same_thread": False}, 
    poolclass=StaticPool
)

@pytest.fixture(name="session")
def session_fixture():
    # Create tables
    SQLModel.metadata.create_all(test_engine)
    
    # Seed required test data
    with Session(test_engine) as session:
        # Seed test data here (user_demo, history items...)
        session.commit()
        
    with Session(test_engine) as session:
        yield session
        
    # Drop tables after test
    SQLModel.metadata.drop_all(test_engine)

@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session
        
    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
```

* **Why this works**: This isolates tests at the database session level. Each test receives a clean transaction/database schema state in an in-memory SQLite database, guaranteeing 100% isolation. It does not touch the disk or interfere with any local `.db` files.
