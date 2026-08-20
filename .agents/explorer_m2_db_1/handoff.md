# Handoff Report — Database Connection Management Refactoring Analysis

## 1. Observation

### 1.1 Existing Database Connection & Lifecycle
- File: `services/api/app/db.py`
- Line 30-40:
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
  This context manager yields a raw SQLite3 connection with `sqlite3.Row` factory enabled, committing on success and closing on exit.

### 1.2 Calling Code Dependency on `sqlite3.Row` Subscripting
- File: `services/api/app/routers/history.py` (Line 16-24):
  ```python
                  id=row["id"],
                  title=row["title"],
                  kind=row["kind"],
                  thumbnail=row["thumbnail"],
                  status=row["status"],
                  size_mb=row["size_mb"],
                  created_at=row["created_at"],
              )
              for row in list_history()
  ```
- File: `services/api/app/routers/tasks.py` (Line 103-104):
  ```python
      progress = int(task["progress"])
      status = task["status"]
  ```
  The router code queries the database and accesses attributes using string subscript keys (`row["id"]`, `task["progress"]`). 

### 1.3 Database Initialization & Seeding
- File: `services/api/app/db.py` (Line 42-98):
  `init_db()` executes raw SQL scripts to create tables `users`, `parse_results`, `tasks`, `history_items`, and `feedback` using `conn.executescript(...)`.
- File: `services/api/app/db.py` (Line 109-163):
  `seed_demo_data()` checks if user `'user_demo'` exists, and inserts default demo records into `users` and `history_items` using raw SQL statement executions.

### 1.4 Unused Data Models
- File: `services/api/app/models.py`
  Contains dataclass-based models of `User`, `ParseResult`, `Task`, `HistoryItem`, and `Feedback`.
- No other Python file in `services/api` imports or references `app.models`, as verified by code search.

### 1.5 Project Dependencies
- File: `services/api/pyproject.toml`
  Dependencies list currently lacks `sqlmodel` or `sqlalchemy` package entries.

### 1.6 Verification Tooling
- Executed command `cmd /c ".venv\Scripts\pytest"` in `services/api/` runs the test suite successfully: `14 passed, 1 warning in 0.33s`.

---

## 2. Logic Chain

1. **Subscript Compatibility**: Because the current callers (e.g. `routers/history.py` and `routers/tasks.py`) access fields using dictionary subscript keys (e.g., `row["id"]`), refactoring directly to SQLModel classes would break with `TypeError: 'Task' object is not subscriptable` if we only return standard SQLModel objects. Therefore, we must implement a subscriptable mixin/base model (`SubscriptableModel`) that overrides `__getitem__` to dynamically return object attributes, preserving full backward compatibility.
2. **Models Replacement**: Since the existing `services/api/app/models.py` defines dataclasses that are completely unused by the codebase, we can safely overwrite it to define all the required SQLModel database tables (`User`, `ParseResult`, `Task`, `HistoryItem`, `Feedback`).
3. **Session & Engine Lifecycle**: We can replace `@contextmanager def connect()` with a contextual `get_session()` wrapper managing a SQLModel `Session(engine)` lifecycle. This context manager will yield a session, commit on success, and rollback on error.
4. **Table creation**: In SQLModel, table DDL creation is performed using `SQLModel.metadata.create_all(engine)`. By calling this inside `init_db()`, we replace the fragile raw DDL SQL string execution completely.
5. **Seeding Refactoring**: We can refactor `seed_demo_data()` using SQLModel's ORM queries (such as `session.get(User, "user_demo")` and counting with `session.exec(select(func.count()).select_from(HistoryItem)).one()`), making database seeding type-safe and database-agnostic.
6. **No Caller Modifications**: By implementing ORM-based wrapper functions inside `db.py` with identical names and signatures as the original raw SQL helper functions, we can refactor the DB layer to SQLModel without modifying a single line of router or worker calling code.

---

## 3. Caveats

- We assumed that any future extensions or third-party packages won't bypass `db.py` to directly connect to SQLite using `sqlite3.connect()`. Since the sqlite3 database path is configured through `Settings.database_path`, any direct SQLite accesses would need to be updated.
- Standard ORM mappings use ISO datetime strings directly stored as SQLite TEXT, matching the original format.

---

## 4. Conclusion

The Raw SQLite3 connection management in `services/api/app/db.py` can be refactored to SQLModel cleanly and with complete backward compatibility. This is achieved by:
1. Defining SQLModel tables in `services/api/app/models.py` utilizing a subscriptable base model.
2. Adding `sqlmodel` to dependencies in `services/api/pyproject.toml`.
3. Updating `services/api/app/db.py` to manage session/engine lifecycles and wrap database CRUD operations under the existing function signatures using SQLModel ORM.
4. Replacing raw DDL and queries in table initialization and seeding with `SQLModel.metadata.create_all` and ORM sessions.

---

## 5. Verification Method

To verify the correctness of the refactoring after implementation:
1. **Verification Command**: Activate the virtual environment and run the test suite:
   ```cmd
   cd services/api
   .venv\Scripts\pytest
   ```
2. **Inspection Files**: Verify that `services/api/app/db.py` and `services/api/app/models.py` contain no raw sqlite3 imports or statements (except for type annotations if any).
3. **Invalidation Condition**: The verification fails if any test fails, or if a router throws a `TypeError` due to subscript access.
