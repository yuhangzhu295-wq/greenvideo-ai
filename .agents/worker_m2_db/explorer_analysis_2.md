# SQLModel Database Schema Refactoring Analysis

## Executive Summary
This report analyzes the refactoring of the SQLite database layer in the `greenvideo-ai` backend to use SQLModel (SQLAlchemy). It outlines the mapping of the five database tables (`users`, `parse_results`, `tasks`, `history_items`, `feedback`) to SQLModel schemas, details how to maintain backward compatibility with existing business logic/endpoints, and details the refactoring of database connection and session management.

---

## 1. Table Analysis & SQLModel Schemas

### Common Model Base
To minimize impact on the business logic endpoints (which access database rows as dictionaries/subscriptable objects, e.g. `row["field"]`), we propose a base class `DictCompatibleModel` that extends `SQLModel` with a `__getitem__` method. This allows all models to remain fully compatible with both dot notation and dictionary-style bracket access.

```python
from sqlmodel import SQLModel

class DictCompatibleModel(SQLModel):
    def __getitem__(self, item: str) -> any:
        """Enables dict-like bracket access for backward compatibility."""
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)
```

---

### 1.1 `users` Table
**Existing SQLite Schema:**
```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    member_level TEXT NOT NULL,
    daily_fast_quota INTEGER NOT NULL
);
```

**Proposed SQLModel Model:**
```python
from sqlmodel import Field

class User(DictCompatibleModel, table=True):
    __tablename__ = "users"
    
    id: str = Field(primary_key=True)
    nickname: str
    member_level: str
    daily_fast_quota: int
```
* **Primary Key:** `id` (string)
* **Constraints:** All columns are non-nullable. In SQLModel, fields without a default value or `Optional` type automatically map to `nullable=False`.

---

### 1.2 `parse_results` Table
**Existing SQLite Schema:**
```sql
CREATE TABLE IF NOT EXISTS parse_results (
    id TEXT PRIMARY KEY,
    url_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    thumbnail TEXT NOT NULL,
    duration INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    formats_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

**Proposed SQLModel Model:**
```python
from datetime import datetime
from sqlmodel import Field

class ParseResult(DictCompatibleModel, table=True):
    __tablename__ = "parse_results"
    
    id: str = Field(primary_key=True)
    url_hash: str
    title: str
    source: str
    thumbnail: str
    duration: int
    published_at: str
    formats_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```
* **Primary Key:** `id` (string)
* **Datetime Handling:** `created_at` mapped to Python's `datetime`. SQLAlchemy converts Python's `datetime` objects to text/ISO strings inside SQLite automatically, while keeping the Python-level objects clean.
* **Complex Data (JSON):** `formats_json` stores the JSON string representation of format lists. We can keep it as `str` to minimize database logic changes, but we can also use a helper property:
  ```python
  import json
  from typing import Any
  
  @property
  def formats(self) -> list[dict[str, Any]]:
      return json.loads(self.formats_json) if self.formats_json else []
  ```

---

### 1.3 `tasks` Table
**Existing SQLite Schema:**
```sql
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL,
    stage TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**Proposed SQLModel Model:**
```python
from datetime import datetime
from typing import Optional
from sqlmodel import Field, Relationship

class Task(DictCompatibleModel, table=True):
    __tablename__ = "tasks"
    
    id: str = Field(primary_key=True)
    type: str
    status: str
    progress: int
    stage: str
    input_json: str
    output_path: Optional[str] = Field(default=None, nullable=True)
    error: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    history_items: list["HistoryItem"] = Relationship(back_populates="task")
```
* **Nullability:** `output_path` and `error` are optional fields (`nullable=True`).
* **Relationship:** Added bidirectional relationship back-populating task's history items to demonstrate best-practice relationships in SQLModel.

---

### 1.4 `history_items` Table
**Existing SQLite Schema:**
```sql
CREATE TABLE IF NOT EXISTS history_items (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    thumbnail TEXT NOT NULL,
    status TEXT NOT NULL,
    size_mb REAL NOT NULL,
    created_at TEXT NOT NULL
);
```

**Proposed SQLModel Model:**
```python
from datetime import datetime
from typing import Optional
from sqlmodel import Field, Relationship

class HistoryItem(DictCompatibleModel, table=True):
    __tablename__ = "history_items"
    
    id: str = Field(primary_key=True)
    task_id: Optional[str] = Field(default=None, foreign_key="tasks.id", nullable=True)
    title: str
    kind: str
    thumbnail: str
    status: str
    size_mb: float
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    task: Optional[Task] = Relationship(back_populates="history_items")
```
* **Foreign Key:** `task_id` has a foreign key relationship to `tasks.id`.
* **Relationship:** Back-populating to `Task`.

---

### 1.5 `feedback` Table
**Existing SQLite Schema:**
```sql
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    contact TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

**Proposed SQLModel Model:**
```python
from datetime import datetime
from sqlmodel import Field

class Feedback(DictCompatibleModel, table=True):
    __tablename__ = "feedback"
    
    id: str = Field(primary_key=True)
    category: str
    description: str
    contact: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 2. Pydantic Alignment & Zero-Change Compatibility

### 2.1 Pydantic DTO Validation
To ensure that endpoints returning Pydantic DTO models (e.g. `HistoryResponse`, `ParseResultDTO`, etc.) can validate SQLModel objects directly, we can add `from_attributes = True` to the model config of each DTO in `app/schemas.py`.

In Pydantic v2:
```python
from pydantic import BaseModel, ConfigDict

class HistoryItemDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    title: str
    kind: str
    thumbnail: str
    status: str
    size_mb: float
    created_at: str  # Pydantic automatically converts Python datetime to ISO/str format
```

This allows the API router code to seamlessly convert database instances to DTOs without manual field mapping:
```python
HistoryItemDTO.model_validate(history_item_orm)
```

### 2.2 Datetime/String Handling
SQLite stores datetime values as strings. SQLModel returns `datetime` objects for datetime columns.
* In `HistoryItemDTO`, `created_at` is annotated as `str`. Pydantic automatically formats Python `datetime` objects to standard string formats when serializing, which prevents any breakage in response validation.

---

## 3. Connection & Session Management Refactoring

We will replace the raw `sqlite3` connection factory in `app/db.py` with a contextual SQLAlchemy/SQLModel engine and session manager.

### Engine and Session Context Manager
```python
from contextlib import contextmanager
from typing import Generator
from sqlmodel import create_engine, Session

from app.config import settings

# Create database engine
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}  # Crucial for SQLite with multi-threaded FastAPI
)

@contextmanager
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

---

## 4. Helper Function Refactoring (CRUD)

To minimize endpoint adjustments, the signature and interface of current helper functions in `app/db.py` are preserved while refactoring their internals to use SQLModel.

### 4.1 Database Initialization
```python
def init_db() -> None:
    ensure_storage()
    # SQLModel will automatically create all tables registered in the metadata
    SQLModel.metadata.create_all(engine)
    seed_demo_data()
```

### 4.2 Insert/Replace Parse Result
```python
import json

def insert_parse_result(row: dict) -> None:
    parse_result = ParseResult(
        id=row["id"],
        url_hash=row["url_hash"],
        title=row["title"],
        source=row["source"],
        thumbnail=row["thumbnail"],
        duration=row["duration"],
        published_at=row["published_at"],
        formats_json=json.dumps(row["formats"], ensure_ascii=False),
    )
    with get_session() as session:
        # merge handles INSERT OR REPLACE behavior
        session.merge(parse_result)
```

### 4.3 Insert Task
```python
def insert_task(row: dict) -> None:
    task = Task(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        progress=row["progress"],
        stage=row["stage"],
        input_json=json.dumps(row.get("input", {}), ensure_ascii=False),
        output_path=row.get("output_path"),
        error=row.get("error"),
    )
    with get_session() as session:
        session.add(task)
```

### 4.4 Update Task
```python
def update_task(task_id: str, **values: object) -> None:
    if not values:
        return
    with get_session() as session:
        task = session.get(Task, task_id)
        if task:
            for key, val in values.items():
                if key == "input":
                    task.input_json = json.dumps(val, ensure_ascii=False)
                else:
                    setattr(task, key, val)
            task.updated_at = datetime.utcnow()
            session.add(task)
```

### 4.5 Get Task
```python
def get_task(task_id: str) -> Task | None:
    with get_session() as session:
        return session.get(Task, task_id)
```

### 4.6 List History
```python
from sqlmodel import select

def list_history() -> list[HistoryItem]:
    with get_session() as session:
        statement = select(HistoryItem).order_by(HistoryItem.created_at.desc(), HistoryItem.id.desc())
        return list(session.exec(statement).all())
```

---

## 5. Summary of Recommended Changes
1. **Dependencies:** Add `sqlmodel` to `dependencies` list in `pyproject.toml`.
2. **`services/api/app/models.py`:** Completely rewrite it to contain the SQLModel classes (`User`, `ParseResult`, `Task`, `HistoryItem`, `Feedback`) inheriting from `DictCompatibleModel`.
3. **`services/api/app/db.py`:** Replace `sqlite3` setup, `connect` context manager, and raw SQL queries with SQLAlchemy engine, `get_session` context manager, and SQLModel queries.
4. **`services/api/app/schemas.py`:** Set `model_config = ConfigDict(from_attributes=True)` on DTOs that will validate SQLModel results.
5. **No Endpoint Code Rewrites Needed:** By using `DictCompatibleModel` and setting `from_attributes=True` on DTOs, endpoint files can remain completely untouched while migrating to a robust, type-safe, and clean SQLModel layer.
