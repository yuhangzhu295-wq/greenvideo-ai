# Analysis Report: GreenVideo AI Backend Features & Test Design

## Executive Summary
This report analyzes the core features, request/response models, database interactions, task handling, and security mechanisms of the GreenVideo AI backend. Based on this analysis, a 4-Tier test suite consisting of 93 test cases is designed to verify the correctness, robustness, and security of the system. Additionally, a directory traversal vulnerability in the file serving route is identified, along with a concrete remediation strategy.

---

## 1. Feature Mapping to API Routes

The backend implements 8 key features. The mapping below details the endpoints, request/response models, schemas, and processing logic.

### Feature 1: User & Quota Management
*   **API Routes**: No dedicated API endpoints currently exist for user management.
*   **Database Schema**: A SQLite `users` table is defined in `db.py`:
    *   `id` (TEXT PRIMARY KEY)
    *   `nickname` (TEXT NOT NULL)
    *   `member_level` (TEXT NOT NULL)
    *   `daily_fast_quota` (INTEGER NOT NULL)
*   **Headers/Tokens**: Currently, no headers or authentication tokens are checked in the API.
*   **Logic**: The system seeds `user_demo` ("视频达人", "普通会员", quota=5) upon DB initialization, but does not enforce authentication or quota constraints in the routers.

### Feature 2: Video Parsing
*   **API Route**: `POST /api/parse`
*   **Request Schema**: `ParseRequest`
    *   `url`: str (min_length=3, max_length=2000)
*   **Response Schema**: `ParseResultDTO`
    *   `id`: str (prefixed with `parse_`)
    *   `title`: str
    *   `source`: str
    *   `thumbnail`: str
    *   `duration`: int (seconds)
    *   `published_at`: str
    *   `formats`: list of `FormatDTO` (`format_id`, `label`, `ext`, `size_mb`)
*   **Headers**: None
*   **Logic**: Uses `VideoDownloader.parse_url()`.
    1. Validates the URL to prevent SSRF and filter login state parameters.
    2. Runs `yt-dlp --dump-json --no-playlist --skip-download --no-warnings <url>`.
    3. If `yt-dlp` is unavailable, or for specific URLs, falls back to a simulated Bilibili/Douyin demo result.
    4. Saves results in the `parse_results` table.

### Feature 3: Video Downloading
*   **API Route**: `POST /api/tasks/download`
*   **Request Schema**: `DownloadTaskRequest`
    *   `parse_id`: str
    *   `format_id`: str
*   **Response Schema**: `TaskCreateResponse`
    *   `task_id`: str (prefixed with `task_`)
    *   `status`: str (returned as `"queued"`)
*   **Headers**: None
*   **Logic**:
    1. Creates a task row in the `tasks` table with status `"queued"` and progress `0`.
    2. Simulates transition to `"processing"` with progress `62` immediately.
    3. Inserts a record into `history_items` with kind `"视频下载"` and status `"processing"`.

### Feature 4: Watermark & Inpaint Processing
*   **API Route**: `POST /api/tasks/watermark` (Supports JSON and Multipart Form-Data)
*   **Request Schema**:
    *   If JSON: `WatermarkTaskRequest`
        *   `source_task_id`: str | None (default `None`)
        *   `mask_regions`: list of `MaskRegion` (`x` >= 0, `y` >= 0, `w` > 0, `h` > 0)
        *   `mode`: str (default `"standard"`, must be `"standard"` or `"ai"`)
    *   If Multipart: File upload (key `file`, supports image/video) and optional form fields.
*   **Response Schema**: `TaskCreateResponse`
    *   `task_id`: str (prefixed with `task_`)
    *   `status`: str (returned as `"queued"`)
*   **Headers**: None
*   **Logic**:
    1. If multipart file is provided, validates the extension (`.mp4`, `.mov`, `.avi`, `.jpg`, `.jpeg`, `.png`) and file size (< 500MB). Saves to `storage/uploads`.
    2. Inserts a task row with progress `62` and status `"processing"`. Stage is `"AI修复中"` (if mode is `"ai"`) or `"模型识别"` (otherwise).
    3. Note: The actual processing is handled by `WatermarkProcessor` using OpenCV and/or the external IOPaint service, but it is currently decoupled from the HTTP route (fully simulated demo).

### Feature 5: Task Status Polling
*   **API Route**: `GET /api/tasks/{task_id}`
*   **Request Params**: `task_id` (path parameter)
*   **Response Schema**: `TaskStatusDTO`
    *   `task_id`: str
    *   `type`: str
    *   `status`: str
    *   `progress`: int
    *   `stage`: str
    *   `eta_seconds`: int
    *   `download_url`: str | None
    *   `error`: str | None
*   **Headers**: None
*   **Logic**:
    1. Retrieves task from the `tasks` table. Returns 404 if not found.
    2. If `progress` >= 100 or `status == "success"`, overrides status to `"success"` and sets `download_url = f"/api/files/{task_id}"`.
    3. Calculates ETA: `max(0, int((100 - progress) * 0.74))`.

### Feature 6: Feedback Submission
*   **API Route**: `POST /api/feedback`
*   **Request Schema**: `FeedbackRequest`
    *   `category`: str (1 to 40 characters)
    *   `description`: str (1 to 500 characters)
    *   `contact`: str (max 120 characters, default `""`)
*   **Response Schema**: `FeedbackResponse`
    *   `ok`: bool (always `True`)
    *   `id`: str (prefixed with `fb_`)
*   **Headers**: None
*   **Logic**: Generates a feedback ID, inserts feedback details into `feedback` table, and returns success.

### Feature 7: History Querying
*   **API Route**: `GET /api/history`
*   **Response Schema**: `HistoryResponse`
    *   `items`: list of `HistoryItemDTO` (`id`, `title`, `kind`, `thumbnail`, `status`, `size_mb`, `created_at`)
*   **Headers**: None
*   **Logic**: Queries the `history_items` table from the database, ordered by `created_at DESC, id DESC`.

### Feature 8: Secure File Serving
*   **API Route**: `GET /api/files/{file_id}`
*   **Request Params**: `file_id` (path parameter)
*   **Response**: `FileResponse`
*   **Headers**: None
*   **Logic**: Resolves the file path using `create_demo_file(file_id)`. Creates a dummy text file at `storage/files/{file_id}.txt` if it does not exist, and streams the file.

---

## 2. Technical Analysis of Tasks, Polling, and Secure File Retrieval

### 2.1 Task Handling & Polling
*   **Demo Mode Design**: In the current implementation, task endpoints (`/api/tasks/download` and `/api/tasks/watermark`) write directly to the SQLite database with `status="processing"` and `progress=62`. The background Celery worker tasks (`workers/tasks.py`) are not actively scheduled by these endpoints.
*   **ETA Estimation**: The ETA is computed on-the-fly as `max(0, int((100 - progress) * 0.74))`. This means at `62%` progress, the ETA is always `28` seconds.

### 2.2 Secure File Serving & Path Traversal Vulnerability
*   **Vulnerable Code in `app/routers/files.py`**:
    ```python
    @router.get("/files/{file_id}")
    def get_file(file_id: str) -> FileResponse:
        path = create_demo_file(file_id)
        return FileResponse(path, filename=f"{file_id}.txt")
    ```
*   **Vulnerable Code in `app/services/storage.py`**:
    ```python
    def storage_path(*parts: str) -> Path:
        ensure_storage()
        path = settings.storage_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def create_demo_file(file_id: str) -> Path:
        path = storage_path("files", f"{file_id}.txt")
        if not path.exists():
            path.write_text("...", encoding="utf-8")
        return path
    ```
*   **Vulnerability Mechanism**:
    `file_id` is passed directly as a path segment to `storage_path("files", f"{file_id}.txt")`. If a user supplies a `file_id` containing path traversal sequences (e.g. `../../etc/passwd` or `..\..\Windows\win.ini`), `settings.storage_dir.joinpath("files", "../../etc/passwd.txt")` resolves to a path outside the designated storage directory.
    *   If the resolved path does not exist, the server attempts to write the demo text to it (creating directories if necessary, which allows arbitrary file creation of `.txt` files).
    *   If the path already exists, the server returns the contents of the file, exposing sensitive system files.
*   **Remediation Strategy**:
    We must validate that the resolved path is strictly within the allowed directory (`storage/files`).
    ```python
    def get_safe_file_path(file_id: str) -> Path:
        base_dir = storage_path("files").resolve()
        # Resolve path and eliminate symlinks/traversal sequences
        target_path = base_dir.joinpath(f"{file_id}.txt").resolve()
        
        # Verify the target path is a child of base_dir
        if not target_path.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="Invalid file ID.")
        return target_path
    ```

---

## 3. Test Suite Design (90+ Test Cases)

Each test case is uniquely identified and mapped to its category. Tests are structured using `pytest` and FastAPI's `TestClient`.

### Test Case Representation Plan
Each test case will be written as a Python function starting with `test_` in pytest. It will mock or utilize the `TestClient` to send requests to the API and perform assertions on the response status, headers, and json payloads. Where appropriate, it will query the SQLite database directly to assert correct data state.

---

### Tier 1: Feature Coverage (40 Tests)

#### Feature 1: User & Quota Management (5 Tests)
*   **TC-1.1.1**: Verify that the seeded user `user_demo` exists in the database.
*   **TC-1.1.2**: Verify that the seeded user has nickname `"视频达人"`.
*   **TC-1.1.3**: Verify that the seeded user has member level `"普通会员"`.
*   **TC-1.1.4**: Verify that the seeded user has daily fast quota set to `5`.
*   **TC-1.1.5**: Verify that a user profile query against database returns correct record fields.

#### Feature 2: Video Parsing (5 Tests)
*   **TC-1.2.1**: Parse valid demo URL `https://example.com/demo` and check 200 OK.
*   **TC-1.2.2**: Parse demo URL and check response matches `ParseResultDTO` structure.
*   **TC-1.2.3**: Parse demo URL and check that formats list contains exactly 3 formats (1080p, 720p, mp3).
*   **TC-1.2.4**: Parse valid http URL (e.g. `http://example.com/demo`) and verify response is mapped successfully.
*   **TC-1.2.5**: Parse a valid URL and verify that a record is successfully written to the `parse_results` database table.

#### Feature 3: Video Downloading (5 Tests)
*   **TC-1.3.1**: Create download task with valid parse ID and format ID `1080p`.
*   **TC-1.3.2**: Create download task with format ID `720p`.
*   **TC-1.3.3**: Create download task with format ID `mp3`.
*   **TC-1.3.4**: Create download task and check that the returned `task_id` starts with `task_`.
*   **TC-1.3.5**: Create download task and check that a corresponding history item with kind `"视频下载"` is created.

#### Feature 4: Watermark & Inpaint Processing (5 Tests)
*   **TC-1.4.1**: Create watermark task via JSON with mode `"standard"` and empty mask regions.
*   **TC-1.4.2**: Create watermark task via JSON with mode `"ai"` and one mask region.
*   **TC-1.4.3**: Create watermark task via Multipart upload with a valid image file.
*   **TC-1.4.4**: Create watermark task via Multipart upload with a valid video file.
*   **TC-1.4.5**: Create watermark task and verify that a task record is added to the `tasks` table.

#### Feature 5: Task Status Polling (5 Tests)
*   **TC-1.5.1**: Poll status of a newly created download task and assert status is `"processing"`.
*   **TC-1.5.2**: Poll status of a task and assert progress is `62`.
*   **TC-1.5.3**: Poll status of a task and verify that the stage description is returned (e.g., `"下载处理中"`).
*   **TC-1.5.4**: Poll status of a successful task and assert that `download_url` is provided.
*   **TC-1.5.5**: Poll status of a task that does not exist and assert 404 Not Found.

#### Feature 6: Feedback Submission (5 Tests)
*   **TC-1.6.1**: Submit feedback with category `"使用建议"` and check 200 OK.
*   **TC-1.6.2**: Submit feedback and verify the response matches `FeedbackResponse` schema.
*   **TC-1.6.3**: Submit feedback and check that the feedback ID starts with `fb_`.
*   **TC-1.6.4**: Submit feedback with a valid description and email contact.
*   **TC-1.6.5**: Submit feedback and verify that the feedback record exists in the database.

#### Feature 7: History Querying (5 Tests)
*   **TC-1.7.1**: Get history list and assert 200 OK.
*   **TC-1.7.2**: Get history list and verify response matches `HistoryResponse` schema.
*   **TC-1.7.3**: Get history list and verify that the 4 default seeded items are present.
*   **TC-1.7.4**: Verify that history items contain details like size, status, and created time.
*   **TC-1.7.5**: Verify that history is returned in descending order of creation time.

#### Feature 8: Secure File Serving (5 Tests)
*   **TC-1.8.1**: Request a file using a valid task ID and assert 200 OK.
*   **TC-1.8.2**: Request a file and verify that the content type is returned.
*   **TC-1.8.3**: Request a file and verify the filename in the header is `{file_id}.txt`.
*   **TC-1.8.4**: Request a file and verify that the body contains the demo placeholder string.
*   **TC-1.8.5**: Request a file that does not exist initially, and confirm the server creates the dummy file.

---

### Tier 2: Boundary & Corner Cases (40 Tests)

#### Feature 1: User & Quota Management (5 Tests)
*   **TC-2.1.1**: Test user lookup with a blank/empty user ID.
*   **TC-2.1.2**: Test user creation with negative quota value (assert DB validation or application handling).
*   **TC-2.1.3**: Test user creation with extremely large quota value (integer overflow).
*   **TC-2.1.4**: Test user creation with invalid/unknown member level.
*   **TC-2.1.5**: Test behavior when querying user table while SQLite database file is locked.

#### Feature 2: Video Parsing (5 Tests)
*   **TC-2.2.1**: Parse URL that is too short (less than 3 characters) and assert Pydantic validation error.
*   **TC-2.2.2**: Parse URL that exceeds 2000 characters and assert validation error.
*   **TC-2.2.3**: Parse URL containing internal IP address (e.g. `http://192.168.1.1/video`) and assert 400 Bad Request.
*   **TC-2.2.4**: Parse URL containing loopback address (e.g. `http://127.0.0.1/video`) and assert 400 Bad Request.
*   **TC-2.2.5**: Parse URL containing sensitive query parameters (e.g. `https://example.com/v?cookie=123`) and assert 400 Bad Request.

#### Feature 3: Video Downloading (5 Tests)
*   **TC-2.3.1**: Create download task with non-existent `parse_id` and assert handling.
*   **TC-2.3.2**: Create download task with empty format ID.
*   **TC-2.3.3**: Create download task with extremely long strings as IDs.
*   **TC-2.3.4**: Create download task with invalid characters in format ID.
*   **TC-2.3.5**: Create download task when the database connections limit is reached.

#### Feature 4: Watermark & Inpaint Processing (5 Tests)
*   **TC-2.4.1**: Create watermark task with negative coordinates in mask region (assert Pydantic validation error).
*   **TC-2.4.2**: Create watermark task with zero width/height in mask region (assert validation error).
*   **TC-2.4.3**: Create watermark task with invalid mode (e.g. `"deep-learning"`, assert validation error).
*   **TC-2.4.4**: Upload file exceeding maximum size limit (e.g., 501 MB) and assert 400 Bad Request.
*   **TC-2.4.5**: Upload file with unauthorized format (e.g., `.exe`, `.txt`) and assert 400 Bad Request.

#### Feature 5: Task Status Polling (5 Tests)
*   **TC-2.5.1**: Poll status with extremely long task ID string.
*   **TC-2.5.2**: Poll status with task ID containing SQL injection payloads (e.g. `' OR 1=1 --`).
*   **TC-2.5.3**: Poll status of a task whose progress is modified to a negative number in the DB.
*   **TC-2.5.4**: Poll status of a task whose progress is modified to >100 in the DB.
*   **TC-2.5.5**: Poll status of a task that has a non-empty error message, confirming it maps correctly.

#### Feature 6: Feedback Submission (5 Tests)
*   **TC-2.6.1**: Submit feedback with empty category and assert validation error.
*   **TC-2.6.2**: Submit feedback with category exceeding 40 characters and assert validation error.
*   **TC-2.6.3**: Submit feedback with description exceeding 500 characters and assert validation error.
*   **TC-2.6.4**: Submit feedback with contact field exceeding 120 characters and assert validation error.
*   **TC-2.6.5**: Submit feedback with empty description and assert validation error.

#### Feature 7: History Querying (5 Tests)
*   **TC-2.7.1**: Get history when database contains no history records.
*   **TC-2.7.2**: Get history with special Unicode/emoji characters in titles.
*   **TC-2.7.3**: Get history when records contain large size values (e.g., 100 GB+).
*   **TC-2.7.4**: Get history when records contain negative size values.
*   **TC-2.7.5**: Get history with invalid/corrupted timestamp format in the database.

#### Feature 8: Secure File Serving (5 Tests)
*   **TC-2.8.1**: Request file with path traversal sequences (`../../etc/passwd`) and assert 400 Bad Request (after patch).
*   **TC-2.8.2**: Request file with Windows-style path traversal (`..\..\Windows\win.ini`) and assert 400 Bad Request.
*   **TC-2.8.3**: Request file with absolute path traversal (`/etc/passwd`) and assert 400 Bad Request.
*   **TC-2.8.4**: Request file with URL-encoded path traversal sequences (`%2e%2e%2f`) and assert 400 Bad Request.
*   **TC-2.8.5**: Request file with special characters (e.g. `*`, `?`, `|`) in `file_id`.

---

### Tier 3: Cross-Feature Combinations (8 Tests)

*   **TC-3.1 (Parse -> Download -> Poll)**: Parse a public video URL, retrieve format details, create a download task using the parsed ID, and immediately poll status.
*   **TC-3.2 (Parse -> Download -> Poll -> Serve)**: Perform parsing, initiate downloading, poll status until progress is 100%, and then retrieve the processed file from the file serving route.
*   **TC-3.3 (Upload -> Watermark -> Poll -> Serve)**: Upload a watermark mask and image, create a watermark removal task, poll status to success, and fetch the final image from the secure file serving endpoint.
*   **TC-3.4 (Watermark -> Poll -> History)**: Create a watermark removal task, poll status, and verify that a new history item appears with matching task ID and type "视频去水印".
*   **TC-3.5 (Download -> Poll -> History)**: Create a download task, poll status, and verify that a new history item appears with matching task ID and type "视频下载".
*   **TC-3.6 (Parse -> Quota Deduction -> Exhaustion)**: Parse a video, create a download task which deducts quota, repeat until quota is exhausted, and verify that subsequent download tasks are rejected.
*   **TC-3.7 (Upload -> Watermark -> Fail -> Poll Error)**: Upload a corrupted video file, trigger watermark processing, let it fail, and verify that status polling returns status "failed" and has a valid error message.
*   **TC-3.8 (History -> Download Re-run)**: Query history, pick a previous successful task, and re-trigger a download task using its parse ID and format ID, verifying it completes successfully.

---

### Tier 4: Real-World Application Scenarios (5 Tests)

*   **TC-4.1 (Regular Video Download Flow)**: A regular user logs in, pastes a Bilibili link, parses it, selects 1080p, starts download, polls progress until success, and downloads the MP4.
*   **TC-4.2 (Video Watermark Removal Flow)**: A content creator uploads a short video (e.g., 20MB MP4), defines a mask region over the bottom-right watermark, triggers watermark removal in AI mode, polls status until completion, and downloads the repaired video.
*   **TC-4.3 (Quota Exhaustion & Recovery Flow)**: A free tier user runs 5 downloads in a row, exhausting their daily limit. The 6th download is blocked. After simulated midnight or manual quota reset, the user is able to parse and download again.
*   **TC-4.4 (Failed Video Parsing and Support Flow)**: A user tries to parse a private/DRM-protected video. The parsing fails with a clear message indicating DRM protection. The user then submits a feedback message explaining the issue with the URL and their email contact.
*   **TC-4.5 (Multi-task Concurrent Operation Flow)**: A user initiates a video download task and concurrently uploads an image to run a watermark removal task. The user queries history to monitor both tasks side by side, verifying their status updates correctly.
