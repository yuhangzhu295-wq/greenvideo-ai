# Backend Exploration Analysis

This document details the findings from the exploration of the backend API service located in `services/api`.

## 1. Endpoints & Routes Overview

The backend is built using FastAPI. The main entry point is `app/main.py`. The endpoints are structured as follows:

| Endpoint | Method | Router | Description | Input Schema | Output Schema / Format |
|---|---|---|---|---|---|
| `/health` | GET | `main.py` | Liveness check | None | `{"status": "ok", "app": "GreenVideo AI"}` |
| `/api/parse` | POST | `routers/parse.py` | Parse video URL metadata using `yt-dlp` or demo mock. | `ParseRequest` | `ParseResultDTO` |
| `/api/tasks/download` | POST | `routers/tasks.py` | Initiate a download task for a specific format. | `DownloadTaskRequest` | `TaskCreateResponse` |
| `/api/tasks/watermark` | POST | `routers/tasks.py` | Initiate a watermark removal task via upload or JSON coordinates. | `WatermarkTaskRequest` or file upload | `TaskCreateResponse` |
| `/api/tasks/{task_id}` | GET | `routers/tasks.py` | Get details and status of a specific task. | None (Path Param) | `TaskStatusDTO` |
| `/api/upload/limits` | GET | `routers/upload.py` | Retrieve file upload constraints. | None | `dict` (limit configurations) |
| `/api/history` | GET | `routers/history.py` | Fetch task history list. | None | `HistoryResponse` |
| `/api/feedback` | POST | `routers/feedback.py` | Submit feedback/issues. | `FeedbackRequest` | `FeedbackResponse` |
| `/api/files/{file_id}` | GET | `routers/files.py` | Download completed tasks/demo files. | None (Path Param) | `FileResponse` |

---

## 2. Database Connection and Schema

The backend uses a local **SQLite** database, configured in `app/config.py` and managed in `app/db.py`.

### Connection Setup
- Connection management is handled via `connect()` context manager which yields `sqlite3.Connection`.
- It executes `ensure_storage()` before connecting to make sure folders (`files`, `uploads`, `static/demo`) exist.
- It sets `conn.row_factory = sqlite3.Row`.

### Database Schema Table Definitions
1. **`users`**
   - `id TEXT PRIMARY KEY`
   - `nickname TEXT NOT NULL`
   - `member_level TEXT NOT NULL`
   - `daily_fast_quota INTEGER NOT NULL`
2. **`parse_results`**
   - `id TEXT PRIMARY KEY`
   - `url_hash TEXT NOT NULL`
   - `title TEXT NOT NULL`
   - `source TEXT NOT NULL`
   - `thumbnail TEXT NOT NULL`
   - `duration INTEGER NOT NULL`
   - `published_at TEXT NOT NULL`
   - `formats_json TEXT NOT NULL`
   - `created_at TEXT NOT NULL`
3. **`tasks`**
   - `id TEXT PRIMARY KEY`
   - `type TEXT NOT NULL` (e.g., `download`, `watermark`)
   - `status TEXT NOT NULL` (e.g., `queued`, `processing`, `success`, `failed`)
   - `progress INTEGER NOT NULL`
   - `stage TEXT NOT NULL`
   - `input_json TEXT NOT NULL` (JSON-serialized input parameters)
   - `output_path TEXT`
   - `error TEXT`
   - `created_at TEXT NOT NULL`
   - `updated_at TEXT NOT NULL`
4. **`history_items`**
   - `id TEXT PRIMARY KEY`
   - `task_id TEXT` (nullable)
   - `title TEXT NOT NULL`
   - `kind TEXT NOT NULL` (e.g., `视频下载`, `视频去水印`)
   - `thumbnail TEXT NOT NULL`
   - `status TEXT NOT NULL`
   - `size_mb REAL NOT NULL`
   - `created_at TEXT NOT NULL`
5. **`feedback`**
   - `id TEXT PRIMARY KEY`
   - `category TEXT NOT NULL`
   - `description TEXT NOT NULL`
   - `contact TEXT NOT NULL`
   - `created_at TEXT NOT NULL`

The corresponding Python dataclasses representing these tables are defined in `app/models.py`.

---

## 3. Security Validators

### A. SSRF URL Validation
Defined in `app/services/security.py` via `validate_public_http_url(url)`:
- **Protocol Restriction**: Must use `http` or `https` schemes.
- **Hostname Resolution check**: Ensures host exists, rejects `localhost` and `localhost.localdomain`.
- **Domain check**: Rejects TLD `.local`.
- **IP Address Blacklisting**: Resolves the hostname (if IP format) and checks against `ipaddress` properties to reject loopback, private, link-local, multicast, reserved, and unspecified IP ranges.
- **Sensitive Parameter Check**: Blocks URLs that contain credentials/login state in query parameters. Checked against `LOGIN_STATE_KEYS` (e.g., `cookie`, `token`, `session`, etc.).

### B. File Upload Sanitization
Defined in `/api/tasks/watermark` (`app/routers/tasks.py`):
- **Extension whitelist**: Only `{".mp4", ".mov", ".avi", ".jpg", ".jpeg", ".png"}` are accepted.
- **File size limit**: Rejects files larger than 500MB (`500 * 1024 * 1024` bytes) dynamically while streaming.
- **Path Traversal avoidance**: Discards original file prefix, extracting only the file extension using `safe_upload_name`. Re-generates filenames with a random UUID/hex string (`upload_<hex>.<ext>`) before saving to `storage/uploads/`.

### C. Potential Path Traversal Vulnerability
In `/api/files/{file_id}` (`app/routers/files.py`) and `create_demo_file(file_id)` (`app/services/storage.py`):
- The `file_id` path parameter is accepted as-is and joined using `Path.joinpath`:
  ```python
  def create_demo_file(file_id: str) -> Path:
      path = storage_path("files", f"{file_id}.txt")
      ...
  ```
  ```python
  def storage_path(*parts: str) -> Path:
      ensure_storage()
      path = settings.storage_dir.joinpath(*parts)
      ...
  ```
- **Vulnerability**: There is no check verifying that the resolved path is strictly under `storage_dir / "files"`. If `file_id` is processed by the server with dot-dot-slash sequence (`../../something`), it may allow reading files outside the designated `files` directory.

---

## 4. API Schemas & Alignments

All API request and response data models are defined using Pydantic in `app/schemas.py`.

- **Input Constraints**:
  - `ParseRequest`: `url` length validation `min_length=3, max_length=2000`.
  - `MaskRegion`: coordinates validated as positive values `x >= 0`, `y >= 0`, `w > 0`, `h > 0`.
  - `WatermarkTaskRequest`: `mode` is validated using pattern regex matching only `standard` or `ai`.
  - `FeedbackRequest`: category (`min 1, max 40`), description (`min 1, max 500`), contact (`max 120`).
- **Alignment with API requirements**:
  - Pydantic models align cleanly with the frontend structure and provide robust schema validation at the HTTP boundary.
