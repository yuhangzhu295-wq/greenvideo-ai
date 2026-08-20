# Database Connection Management SQLModel Refactoring Analysis

This report outlines the current raw `sqlite3` connection management and details the design and implementation plan to refactor it using **SQLModel** (which uses SQLAlchemy under the hood). 

---

## 1. Current SQLite3 Database Setup and Connection Management

### 1.1 Connection Lifecycle
Currently, `services/api/app/db.py` manages connections via a context manager:
```python
@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    ensure_storage()
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
```
- **Transaction & Commit**: It yields a connection and automatically commits any updates on exit.
- **Row Factory**: It sets `conn.row_factory = sqlite3.Row`. This is critical because it allows calling code to access columns as dictionary keys (e.g., `row["id"]`), which routers in `routers/tasks.py` and `routers/history.py` rely on.
- **Resource Management**: It strictly closes the connection in a `finally` block.

### 1.2 Table Creation & Initialization
On application startup, `app.main.create_app()` calls `init_db()`.
- **`init_db()`** executes raw DDL statements via `conn.executescript(...)` to create the following tables if they do not exist:
  - `users`: stores `id`, `nickname`, `member_level`, `daily_fast_quota`.
  - `parse_results`: stores `id`, `url_hash`, `title`, `source`, `thumbnail`, `duration`, `published_at`, `formats_json`, `created_at`.
  - `tasks`: stores `id`, `type`, `status`, `progress`, `stage`, `input_json`, `output_path`, `error`, `created_at`, `updated_at`.
  - `history_items`: stores `id`, `task_id`, `title`, `kind`, `thumbnail`, `status`, `size_mb`, `created_at`.
  - `feedback`: stores `id`, `category`, `description`, `contact`, `created_at`.
- It then calls `seed_demo_data()`.

### 1.3 Data Seeding
- **`seed_demo_data()`** runs raw SQL to check:
  - If a user with ID `'user_demo'` exists; if not, inserts it.
  - If the count of `history_items` is `0`; if so, inserts 4 demo history items.

### 1.4 Unused dataclasses
`services/api/app/models.py` currently contains `dataclass` representations of these models, but they are **never** imported or used anywhere in the codebase. This allows us to replace `models.py` completely with SQLModel models.

---

## 2. SQLModel Refactoring Design

We will replace raw SQL execution with SQLModel. To prevent modifying the callers (in routers and workers) and preserve backward compatibility, we will design the refactoring carefully.

### 2.1 Dependency Changes
Add `sqlmodel` to `services/api/pyproject.toml`:
```toml
dependencies = [
  ...
  "sqlmodel>=0.0.22"
]
```

### 2.2 Model Definitions (`services/api/app/models.py`)
To maintain backward compatibility with code that accesses rows using subscript syntax (e.g., `row["id"]` instead of `row.id`), we introduce a `SubscriptableModel` helper base class that overrides `__getitem__`:

```python
from __future__ import annotations
from typing import Any
from sqlmodel import SQLModel, Field

class SubscriptableModel(SQLModel):
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc
```

The tables will be mapped to SQLModel classes inheriting from `SubscriptableModel`:

```python
class User(SubscriptableModel, table=True):
    __tablename__ = "users"
    id: str = Field(primary_key=True)
    nickname: str
    member_level: str
    daily_fast_quota: int


class ParseResult(SubscriptableModel, table=True):
    __tablename__ = "parse_results"
    id: str = Field(primary_key=True)
    url_hash: str
    title: str
    source: str
    thumbnail: str
    duration: int
    published_at: str
    formats_json: str
    created_at: str


class Task(SubscriptableModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(primary_key=True)
    type: str
    status: str
    progress: int
    stage: str
    input_json: str
    output_path: str | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: str
    updated_at: str


class HistoryItem(SubscriptableModel, table=True):
    __tablename__ = "history_items"
    id: str = Field(primary_key=True)
    task_id: str | None = Field(default=None)
    title: str
    kind: str
    thumbnail: str
    status: str
    size_mb: float
    created_at: str


class Feedback(SubscriptableModel, table=True):
    __tablename__ = "feedback"
    id: str = Field(primary_key=True)
    category: str
    description: str
    contact: str
    created_at: str
```

### 2.3 Connection and Contextual Session Management (`services/api/app/db.py`)
Instead of `sqlite3.connect()`, we create a SQLModel/SQLAlchemy engine and manage sessions.

```python
from sqlmodel import create_engine, Session, select, func
from app.config import settings

# Create engine with connect_args for SQLite multi-thread support
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
)

@contextmanager
def get_session() -> Iterable[Session]:
    ensure_storage()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

For FastAPI dependency injection, we can define:
```python
def get_db_session() -> Iterable[Session]:
    with Session(engine) as session:
        yield session
```
*(Since existing DB wrappers manage their own session, FastAPI routes won't need to inject sessions directly, preserving exact caller compatibility.)*

### 2.4 Refactoring DB CRUD Wrapper Methods
We will rewrite the CRUD functions in `db.py` to use SQLModel ORM:

```python
import json
from app.models import User, ParseResult, Task, HistoryItem, Feedback

def insert_parse_result(row: dict) -> None:
    parsed = ParseResult(
        id=row["id"],
        url_hash=row["url_hash"],
        title=row["title"],
        source=row["source"],
        thumbnail=row["thumbnail"],
        duration=row["duration"],
        published_at=row["published_at"],
        formats_json=json.dumps(row["formats"], ensure_ascii=False),
        created_at=utc_now(),
    )
    with get_session() as session:
        session.merge(parsed) # Handles INSERT OR REPLACE behavior


def insert_task(row: dict) -> None:
    now = utc_now()
    task = Task(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        progress=row["progress"],
        stage=row["stage"],
        input_json=json.dumps(row.get("input", {}), ensure_ascii=False),
        output_path=row.get("output_path"),
        error=row.get("error"),
        created_at=now,
        updated_at=now,
    )
    with get_session() as session:
        session.add(task)


def update_task(task_id: str, **values: object) -> None:
    if not values:
        return
    with get_session() as session:
        task = session.get(Task, task_id)
        if task:
            for key, val in values.items():
                setattr(task, key, val)
            task.updated_at = utc_now()
            session.add(task)


def get_task(task_id: str) -> Task | None:
    # A read-only session is sufficient here
    with Session(engine) as session:
        return session.get(Task, task_id)


def insert_history(row: dict) -> None:
    history = HistoryItem(
        id=row["id"],
        task_id=row.get("task_id"),
        title=row["title"],
        kind=row["kind"],
        thumbnail=row["thumbnail"],
        status=row["status"],
        size_mb=row["size_mb"],
        created_at=row["created_at"],
    )
    with get_session() as session:
        session.add(history)


def list_history() -> list[HistoryItem]:
    with Session(engine) as session:
        statement = select(HistoryItem).order_by(HistoryItem.created_at.desc(), HistoryItem.id.desc())
        return list(session.exec(statement).all())


def insert_feedback(row: dict) -> None:
    fb = Feedback(
        id=row["id"],
        category=row["category"],
        description=row["description"],
        contact=row.get("contact", ""),
        created_at=utc_now(),
    )
    with get_session() as session:
        session.add(fb)
```

### 2.5 Refactoring Table Creation
Modify `init_db()` to automatically create tables from SQLModel metadata:
```python
def init_db() -> None:
    ensure_storage()
    # Create all tables defined in SQLModel metadata
    SQLModel.metadata.create_all(engine)
    seed_demo_data()
```

### 2.6 Refactoring Data Seeding
Modify `seed_demo_data()` to query using SQLModel:
```python
def seed_demo_data() -> None:
    with get_session() as session:
        # Check user existence
        user_exists = session.get(User, "user_demo")
        if not user_exists:
            user = User(
                id="user_demo",
                nickname="视频达人",
                member_level="普通会员",
                daily_fast_quota=5
            )
            session.add(user)

        # Check history item count
        count = session.exec(select(func.count()).select_from(HistoryItem)).one()
        if count == 0:
            items = [
                HistoryItem(
                    id="hist_aurora",
                    task_id=None,
                    title="极光之旅的治愈瞬间",
                    kind="视频下载",
                    thumbnail=DEMO_THUMBNAILS[0],
                    status="success",
                    size_mb=128.6,
                    created_at="2024-05-15 14:32:18",
                ),
                HistoryItem(
                    id="hist_sunset",
                    task_id=None,
                    title="海边日落延时摄影",
                    kind="视频去水印",
                    thumbnail=DEMO_THUMBNAILS[1],
                    status="success",
                    size_mb=96.3,
                    created_at="2024-05-15 10:21:07",
                ),
                HistoryItem(
                    id="hist_city",
                    task_id=None,
                    title="城市夜景4K素材",
                    kind="视频下载",
                    thumbnail=DEMO_THUMBNAILS[2],
                    status="processing",
                    size_mb=256.8,
                    created_at="2024-05-14 21:45:33",
                ),
                HistoryItem(
                    id="hist_coffee",
                    task_id=None,
                    title="咖啡拉花教学视频",
                    kind="视频去水印",
                    thumbnail=DEMO_THUMBNAILS[3],
                    status="failed",
                    size_mb=72.4,
                    created_at="2024-05-13 18:09:52",
                ),
            ]
            for item in items:
                session.add(item)
```

---

## 3. Step-by-Step Refactoring Implementation Plan

1. **Install SQLModel**: Add `"sqlmodel>=0.0.22"` to dependencies in `services/api/pyproject.toml`.
2. **Re-define Models**: Replace `services/api/app/models.py` with the `SubscriptableModel` base class and the definitions for `User`, `ParseResult`, `Task`, `HistoryItem`, and `Feedback`.
3. **Refactor DB logic**: Replace raw SQLite3 logic in `services/api/app/db.py` with SQLModel logic (engine, session-based context manager, and ORM wrapper functions).
4. **Clean up DB migrations (if any)**: Verify that the DB file is automatically created inside the `./storage` folder on app start.
5. **Run tests**: Activate the venv and execute `.venv/Scripts/pytest` inside `services/api` to verify functionality.
