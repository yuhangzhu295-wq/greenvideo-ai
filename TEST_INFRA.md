# GreenVideo AI Testing Infrastructure Specification

This document outlines the testing philosophy, infrastructure design, mocking strategy, feature inventory, and execution guidelines for the GreenVideo AI project. It includes a comprehensive list of all 93 test cases across 4 testing tiers.

---

## 1. Testing Philosophy

To ensure reliability, security, and a seamless user experience across platforms, the GreenVideo AI project follows a strict testing philosophy consisting of:

### 1.1 Category-Partition & Boundary Value Analysis (BVA)
Every API route and service function must have its input domain divided into discrete, mutually exclusive partitions:
*   **Valid partitions**: Representative inputs verifying the happy path.
*   **Boundary partitions**: Values at the edges of validity (e.g., maximum string lengths, coordinate borders, exact file size thresholds).
*   **Invalid partitions**: Out-of-bounds, malformed, or hostile inputs (e.g., SQL injections, path traversal sequences, private IPs).

### 1.2 Graceful Degradation & Sandbox Isolation (Mocking)
GreenVideo AI relies on heavy external dependencies (`yt-dlp`, `ffmpeg`, OpenCV, and IOPaint).
*   **Local/CI Portability**: Tests must run successfully on any machine without requiring actual GPU hardware, local Python model environments, or system-level binary dependencies.
*   **Subprocess Mocking**: System calls to `yt-dlp` or `ffmpeg` are intercepted or mocked in the test suite using mock configurations, falling back to deterministic demo data when the real binaries are not found.
*   **Network Sandboxing**: Any connection to external services (such as the IOPaint HTTP endpoint) is sandboxed using mocks or httpx mock clients, avoiding real network calls.

### 1.3 Transactional / Isolated Databases
Tests must not write to or corrupt the development SQLite database (`storage/greenvideo.db`).
*   An in-memory SQLite database or a dedicated temporary SQLite file (`storage/test_greenvideo.db`) is instantiated for each test run.
*   Setup and teardown fixtures ensure a clean slate before every test case execution.

---

## 2. Test Suite Architecture

The test suite is structured into 4 distinct tiers:

```
[ Tier 4: Real-World Application Scenarios ]
                      ▲
     [ Tier 3: Pairwise Combinations ]
                      ▲
     [ Tier 2: Boundary & Corner Cases ]
                      ▲
     [ Tier 1: Feature Coverage (Happy Path) ]
```

### 2.1 Directory Layout
In accordance with layout compliance rules, tests are co-located within the respective modules:
*   `services/api/tests/` - Backend test suite.
    *   `conftest.py` - Core test fixtures (database, client, mock services).
    *   `test_api.py` - Endpoint validation tests.
    *   `test_security.py` - Security boundary and path traversal validation.
    *   `test_services.py` - Unit tests for media, downloader, and storage services.
*   `apps/miniapp/` - Frontend testing specifications (e.g., Jest or React Testing Library configs, co-located in page folders).

---

## 3. Mocking & Stubbing Strategy

### 3.1 Subprocess Mocking (`yt-dlp` / `ffmpeg`)
Instead of running subprocesses directly, a pytest fixture wraps the execution check. If the required binary is missing, it mocks the return codes and standard outputs:
```python
@pytest.fixture
def mock_subprocess(monkeypatch):
    def mock_run(args, **kwargs):
        class MockCompletedProcess:
            returncode = 0
            stdout = '{"title": "Test Title", "formats": [{"format_id": "1080p", "ext": "mp4", "filesize": 104857600}]}'
            stderr = ""
        return MockCompletedProcess()
    monkeypatch.setattr("subprocess.run", mock_run)
```

### 3.2 Mocking IOPaint AI Endpoint
HTTP requests to the AI inpainting server must be intercepted:
```python
@pytest.fixture
def mock_iopaint_service(respx_mock):
    respx_mock.get("http://127.0.0.1:8080/api/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx_mock.post("http://127.0.0.1:8080/api/inpaint").mock(
        return_value=httpx.Response(200, json={"image": "base64_encoded_dummy_image_data..."})
    )
```

---

## 4. Feature Inventory

The GreenVideo AI backend implements 8 key features:

1.  **User & Quota Management**: Manages users (`users` table) and tracks daily fast quota usage.
2.  **Video Parsing**: Extracts titles, formats, and other metadata from video URLs via `VideoDownloader`.
3.  **Video Downloading**: Spawns and manages async/mock download tasks.
4.  **Watermark/Inpaint Processing**: Applies OpenCV/AI inpaints on specified mask regions of images/videos.
5.  **Task Status Polling**: Returns progress, status, ETA, stage, and download URLs of async tasks.
6.  **Feedback Submission**: Records user feedback categories, descriptions, and contact info in the `feedback` table.
7.  **History Querying**: Lists and filters past task histories in descending chronological order.
8.  **Secure File Serving**: Serves generated output files safely, preventing path traversal and arbitrary file reads.

---

## 5. Complete Test Case Specification (93 Test Cases)

Below is the complete registry of all 93 test cases across the 4 Tiers.

### Tier 1: Feature Coverage (40 Tests)

#### Feature 1: User & Quota Management (5 Tests)
*   **TC-1.1.1**: Verify seeded user `user_demo` exists in the database.
    *   *Target Route/Behavior*: Query `users` database table directly. Assert record count >= 1 and `id == "user_demo"`.
*   **TC-1.1.2**: Verify that the seeded user has nickname `"视频达人"`.
    *   *Target Route/Behavior*: Query `users` table directly. Assert `nickname == "视频达人"`.
*   **TC-1.1.3**: Verify that the seeded user has member level `"普通会员"`.
    *   *Target Route/Behavior*: Query `users` table directly. Assert `member_level == "普通会员"`.
*   **TC-1.1.4**: Verify that the seeded user has daily fast quota set to `5`.
    *   *Target Route/Behavior*: Query `users` table directly. Assert `daily_fast_quota == 5`.
*   **TC-1.1.5**: Verify that a user profile query against database returns correct record fields.
    *   *Target Route/Behavior*: Query `users` table directly. Validate that all expected columns (id, nickname, member_level, daily_fast_quota) are present and have correct data types.

#### Feature 2: Video Parsing (5 Tests)
*   **TC-1.2.1**: Parse valid demo URL `https://example.com/demo` and check 200 OK.
    *   *Target Route/Behavior*: `POST /api/parse`. Assert response status code is `200` and response matches expected structure.
*   **TC-1.2.2**: Parse demo URL and check response matches `ParseResultDTO` structure.
    *   *Target Route/Behavior*: `POST /api/parse`. Validate fields: `id` starts with `parse_`, `title`, `source`, `thumbnail`, `duration`, `published_at`, and `formats`.
*   **TC-1.2.3**: Parse demo URL and check that formats list contains exactly 3 formats (1080p, 720p, mp3).
    *   *Target Route/Behavior*: `POST /api/parse`. Inspect response formats array. Verify formats have correct IDs and labels.
*   **TC-1.2.4**: Parse valid http URL (e.g. `http://example.com/demo`) and verify response is mapped successfully.
    *   *Target Route/Behavior*: `POST /api/parse` with HTTP URL. Assert HTTP 200 and successful fallback/parsing.
*   **TC-1.2.5**: Parse a valid URL and verify that a record is successfully written to the `parse_results` database table.
    *   *Target Route/Behavior*: `POST /api/parse`. Inspect SQLite `parse_results` table for a record with matching parse ID.

#### Feature 3: Video Downloading (5 Tests)
*   **TC-1.3.1**: Create download task with valid parse ID and format ID `1080p`.
    *   *Target Route/Behavior*: `POST /api/tasks/download`. Payload: `{"parse_id": "parse_demo", "format_id": "1080p"}`. Assert status 200.
*   **TC-1.3.2**: Create download task with format ID `720p`.
    *   *Target Route/Behavior*: `POST /api/tasks/download`. Payload: `{"parse_id": "parse_demo", "format_id": "720p"}`. Assert status 200 and task ID starts with `task_`.
*   **TC-1.3.3**: Create download task with format ID `mp3`.
    *   *Target Route/Behavior*: `POST /api/tasks/download`. Payload: `{"parse_id": "parse_demo", "format_id": "mp3"}`. Assert status 200 and task ID starts with `task_`.
*   **TC-1.3.4**: Create download task and check that the returned `task_id` starts with `task_`.
    *   *Target Route/Behavior*: `POST /api/tasks/download`. Extract `task_id` from JSON response, verify prefix is `task_` and status is `queued`.
*   **TC-1.3.5**: Create download task and check that a corresponding history item with kind `"视频下载"` is created.
    *   *Target Route/Behavior*: `POST /api/tasks/download`. Query database `history_items` table. Assert that an item with matching task ID and `kind == "视频下载"` exists.

#### Feature 4: Watermark & Inpaint Processing (5 Tests)
*   **TC-1.4.1**: Create watermark task via JSON with mode `"standard"` and empty mask regions.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark`. Payload: `{"source_task_id": "task_demo", "mask_regions": [], "mode": "standard"}`. Assert status 200.
*   **TC-1.4.2**: Create watermark task via JSON with mode `"ai"` and one mask region.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark`. Payload: `{"source_task_id": "task_demo", "mask_regions": [{"x": 10, "y": 10, "w": 40, "h": 20}], "mode": "ai"}`. Assert status 200.
*   **TC-1.4.3**: Create watermark task via Multipart upload with a valid image file.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark`. Submit multipart request with file content (JPEG) and fields. Assert status 200.
*   **TC-1.4.4**: Create watermark task via Multipart upload with a valid video file.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark`. Submit multipart request with file content (MP4) and fields. Assert status 200.
*   **TC-1.4.5**: Create watermark task and verify that a task record is added to the `tasks` table.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark`. Query database `tasks` table. Assert a task is inserted with status `"processing"` or `"queued"`.

#### Feature 5: Task Status Polling (5 Tests)
*   **TC-1.5.1**: Poll status of a newly created download task and assert status is `"processing"`.
    *   *Target Route/Behavior*: `GET /api/tasks/{task_id}`. Assert status is `"processing"`.
*   **TC-1.5.2**: Poll status of a task and assert progress is `62`.
    *   *Target Route/Behavior*: `GET /api/tasks/{task_id}`. Assert progress is `62`.
*   **TC-1.5.3**: Poll status of a task and verify that the stage description is returned (e.g., `"下载处理中"`).
    *   *Target Route/Behavior*: `GET /api/tasks/{task_id}`. Assert response contains non-empty `stage` field.
*   **TC-1.5.4**: Poll status of a successful task and assert that `download_url` is provided.
    *   *Target Route/Behavior*: Set status to `"success"` / progress to 100 in the DB, then query `GET /api/tasks/{task_id}`. Assert `status == "success"` and `download_url` is non-null.
*   **TC-1.5.5**: Poll status of a task that does not exist and assert 404 Not Found.
    *   *Target Route/Behavior*: `GET /api/tasks/task_invalid`. Assert status code is `404`.

#### Feature 6: Feedback Submission (5 Tests)
*   **TC-1.6.1**: Submit feedback with category `"使用建议"` and check 200 OK.
    *   *Target Route/Behavior*: `POST /api/feedback`. Payload: `{"category": "使用建议", "description": "some description", "contact": ""}`. Assert status 200.
*   **TC-1.6.2**: Submit feedback and verify the response matches `FeedbackResponse` schema.
    *   *Target Route/Behavior*: `POST /api/feedback`. Assert response contains keys `ok` (boolean) and `id` (string).
*   **TC-1.6.3**: Submit feedback and check that the feedback ID starts with `fb_`.
    *   *Target Route/Behavior*: `POST /api/feedback`. Assert `id` field prefix is `fb_`.
*   **TC-1.6.4**: Submit feedback with a valid description and email contact.
    *   *Target Route/Behavior*: `POST /api/feedback`. Payload: `{"category": "使用建议", "description": "details", "contact": "user@example.com"}`. Assert status 200.
*   **TC-1.6.5**: Submit feedback and verify that the feedback record exists in the database.
    *   *Target Route/Behavior*: `POST /api/feedback`. Query SQLite database `feedback` table. Verify record is present with correct fields.

#### Feature 7: History Querying (5 Tests)
*   **TC-1.7.1**: Get history list and assert 200 OK.
    *   *Target Route/Behavior*: `GET /api/history`. Assert status code is 200.
*   **TC-1.7.2**: Get history list and verify response matches `HistoryResponse` schema.
    *   *Target Route/Behavior*: `GET /api/history`. Validate structure contains list of items with proper field constraints.
*   **TC-1.7.3**: Get history list and verify that the 4 default seeded items are present.
    *   *Target Route/Behavior*: `GET /api/history`. Assert items length >= 4 and check presence of seeded entries.
*   **TC-1.7.4**: Verify that history items contain details like size, status, and created time.
    *   *Target Route/Behavior*: `GET /api/history`. Assert that details like `size_mb`, `status`, and `created_at` are present on returned items.
*   **TC-1.7.5**: Verify that history is returned in descending order of creation time.
    *   *Target Route/Behavior*: `GET /api/history`. Compare creation timestamps of adjacent list items to ensure chronological descending ordering.

#### Feature 8: Secure File Serving (5 Tests)
*   **TC-1.8.1**: Request a file using a valid task ID and assert 200 OK.
    *   *Target Route/Behavior*: `GET /api/files/{file_id}`. Assert status code is 200.
*   **TC-1.8.2**: Request a file and verify that the content type is returned.
    *   *Target Route/Behavior*: `GET /api/files/{file_id}`. Verify `Content-Type` header (e.g. `text/plain` or stream/attachment type).
*   **TC-1.8.3**: Request a file and verify the filename in the header is `{file_id}.txt`.
    *   *Target Route/Behavior*: `GET /api/files/{file_id}`. Verify `Content-Disposition` contains `filename="{file_id}.txt"`.
*   **TC-1.8.4**: Request a file and verify that the body contains the demo placeholder string.
    *   *Target Route/Behavior*: `GET /api/files/{file_id}`. Read response body and assert it contains `"..."` or expected content.
*   **TC-1.8.5**: Request a file that does not exist initially, and confirm the server creates the dummy file.
    *   *Target Route/Behavior*: Check directory for `storage/files/{file_id}.txt` before and after request `GET /api/files/{file_id}`. Verify it is created and served.

---

### Tier 2: Boundary & Corner Cases (40 Tests)

#### Feature 1: User & Quota Management (5 Tests)
*   **TC-2.1.1**: Test user lookup with a blank/empty user ID.
    *   *Target Route/Behavior*: Query database/API layer for user `""`. Verify appropriate fallback, exception, or empty result handling.
*   **TC-2.1.2**: Test user creation with negative quota value (assert DB validation or application handling).
    *   *Target Route/Behavior*: Insert row in SQLite `users` table with `daily_fast_quota = -5`. Check validation constraints/handling.
*   **TC-2.1.3**: Test user creation with extremely large quota value (integer overflow).
    *   *Target Route/Behavior*: Insert row in SQLite `users` table with `daily_fast_quota = 999999999999`. Verify no overflow failure.
*   **TC-2.1.4**: Test user creation with invalid/unknown member level.
    *   *Target Route/Behavior*: Insert row in SQLite `users` table with `member_level = "InvalidLevel"`. Verify behavior (check if validation throws or handles safely).
*   **TC-2.1.5**: Test behavior when querying user table while SQLite database file is locked.
    *   *Target Route/Behavior*: Simulate database lock condition, verify query handles connection retry/timeout gracefully.

#### Feature 2: Video Parsing (5 Tests)
*   **TC-2.2.1**: Parse URL that is too short (less than 3 characters) and assert Pydantic validation error.
    *   *Target Route/Behavior*: `POST /api/parse` with `{"url": "ab"}`. Assert status code 422 (Unprocessable Entity).
*   **TC-2.2.2**: Parse URL that exceeds 2000 characters and assert validation error.
    *   *Target Route/Behavior*: `POST /api/parse` with URL of 2001 characters. Assert status code 422 (Unprocessable Entity).
*   **TC-2.2.3**: Parse URL containing internal IP address (e.g. `http://192.168.1.1/video`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `POST /api/parse` with internal URL. Assert status code 400 (SSRF protection).
*   **TC-2.2.4**: Parse URL containing loopback address (e.g. `http://127.0.0.1/video`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `POST /api/parse` with loopback URL. Assert status code 400 (SSRF protection).
*   **TC-2.2.5**: Parse URL containing sensitive query parameters (e.g. `https://example.com/v?cookie=123`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `POST /api/parse` with credential/state-carrying URL query parameters. Assert status code 400.

#### Feature 3: Video Downloading (5 Tests)
*   **TC-2.3.1**: Create download task with non-existent `parse_id` and assert handling.
    *   *Target Route/Behavior*: `POST /api/tasks/download` with `{"parse_id": "nonexistent", "format_id": "1080p"}`. Assert status code (400 or 404).
*   **TC-2.3.2**: Create download task with empty format ID.
    *   *Target Route/Behavior*: `POST /api/tasks/download` with `{"parse_id": "parse_demo", "format_id": ""}`. Assert validation error (422).
*   **TC-2.3.3**: Create download task with extremely long strings as IDs.
    *   *Target Route/Behavior*: `POST /api/tasks/download` with extremely long parse_id. Verify validation limits.
*   **TC-2.3.4**: Create download task with invalid characters in format ID.
    *   *Target Route/Behavior*: `POST /api/tasks/download` with `format_id = "1080p; DROP TABLE tasks"`. Assert validation rejection.
*   **TC-2.3.5**: Create download task when the database connections limit is reached.
    *   *Target Route/Behavior*: Simulate database exhaustion. Verify system returns 500/503 gracefully rather than crashing.

#### Feature 4: Watermark & Inpaint Processing (5 Tests)
*   **TC-2.4.1**: Create watermark task with negative coordinates in mask region (assert Pydantic validation error).
    *   *Target Route/Behavior*: `POST /api/tasks/watermark` with mask region `{"x": -10, "y": 10, "w": 40, "h": 20}`. Assert status code 422.
*   **TC-2.4.2**: Create watermark task with zero width/height in mask region (assert validation error).
    *   *Target Route/Behavior*: `POST /api/tasks/watermark` with `{"x": 10, "y": 10, "w": 0, "h": 20}`. Assert status code 422.
*   **TC-2.4.3**: Create watermark task with invalid mode (e.g. `"deep-learning"`, assert validation error).
    *   *Target Route/Behavior*: `POST /api/tasks/watermark` with `mode = "deep-learning"`. Assert status code 422.
*   **TC-2.4.4**: Upload file exceeding maximum size limit (e.g., 501 MB) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark` with multipart file of 501MB size. Assert status code 400.
*   **TC-2.4.5**: Upload file with unauthorized format (e.g., `.exe`, `.txt`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `POST /api/tasks/watermark` with file extension `.exe`. Assert status code 400.

#### Feature 5: Task Status Polling (5 Tests)
*   **TC-2.5.1**: Poll status with extremely long task ID string.
    *   *Target Route/Behavior*: `GET /api/tasks/{extremely_long_id}`. Assert validation or 404 response.
*   **TC-2.5.2**: Poll status with task ID containing SQL injection payloads (e.g. `' OR 1=1 --`).
    *   *Target Route/Behavior*: `GET /api/tasks/' OR 1=1 --`. Assert 404 response (path/parameter SQL injection protection).
*   **TC-2.5.3**: Poll status of a task whose progress is modified to a negative number in the DB.
    *   *Target Route/Behavior*: Inject `progress = -5` in database task row. Call `GET /api/tasks/{task_id}`. Assert API handles or coerces it safely.
*   **TC-2.5.4**: Poll status of a task whose progress is modified to >100 in the DB.
    *   *Target Route/Behavior*: Inject `progress = 150` in database task row. Call `GET /api/tasks/{task_id}`. Assert API coerces/reports it as success/100%.
*   **TC-2.5.5**: Poll status of a task that has a non-empty error message, confirming it maps correctly.
    *   *Target Route/Behavior*: Inject `error = "Inpaint server timeout"` and `status = "failed"` in DB. Call `GET /api/tasks/{task_id}`. Assert response has matching error/status fields.

#### Feature 6: Feedback Submission (5 Tests)
*   **TC-2.6.1**: Submit feedback with empty category and assert validation error.
    *   *Target Route/Behavior*: `POST /api/feedback` with `category = ""`. Assert status code 422.
*   **TC-2.6.2**: Submit feedback with category exceeding 40 characters and assert validation error.
    *   *Target Route/Behavior*: `POST /api/feedback` with category of 41 characters. Assert status code 422.
*   **TC-2.6.3**: Submit feedback with description exceeding 500 characters and assert validation error.
    *   *Target Route/Behavior*: `POST /api/feedback` with description of 501 characters. Assert status code 422.
*   **TC-2.6.4**: Submit feedback with contact field exceeding 120 characters and assert validation error.
    *   *Target Route/Behavior*: `POST /api/feedback` with contact of 121 characters. Assert status code 422.
*   **TC-2.6.5**: Submit feedback with empty description and assert validation error.
    *   *Target Route/Behavior*: `POST /api/feedback` with `description = ""`. Assert status code 422.

#### Feature 7: History Querying (5 Tests)
*   **TC-2.7.1**: Get history when database contains no history records.
    *   *Target Route/Behavior*: Truncate `history_items` table. Call `GET /api/history`. Assert 200 OK with empty items list `[]`.
*   **TC-2.7.2**: Get history with special Unicode/emoji characters in titles.
    *   *Target Route/Behavior*: Insert Unicode/emoji title in history. Call `GET /api/history`. Verify JSON encoding and text integrity.
*   **TC-2.7.3**: Get history when records contain large size values (e.g., 100 GB+).
    *   *Target Route/Behavior*: Insert history record with size `102400` (MB). Call `GET /api/history`. Verify correct output representation.
*   **TC-2.7.4**: Get history when records contain negative size values.
    *   *Target Route/Behavior*: Insert history record with negative size. Verify safe rendering/handling.
*   **TC-2.7.5**: Get history with invalid/corrupted timestamp format in the database.
    *   *Target Route/Behavior*: Insert invalid ISO date string in history record. Verify API handles format parsing or degrades gracefully.

#### Feature 8: Secure File Serving (5 Tests)
*   **TC-2.8.1**: Request file with path traversal sequences (`../../etc/passwd`) and assert 400 Bad Request (after security patch).
    *   *Target Route/Behavior*: `GET /api/files/../../etc/passwd`. Assert status code 400.
*   **TC-2.8.2**: Request file with Windows-style path traversal (`..\..\Windows\win.ini`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `GET /api/files/..\..\Windows\win.ini`. Assert status code 400.
*   **TC-2.8.3**: Request file with absolute path traversal (`/etc/passwd`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `GET /api/files//etc/passwd`. Assert status code 400.
*   **TC-2.8.4**: Request file with URL-encoded path traversal sequences (`%2e%2e%2f`) and assert 400 Bad Request.
    *   *Target Route/Behavior*: `GET /api/files/%2e%2e%2fetc%2fpasswd`. Assert status code 400.
*   **TC-2.8.5**: Request file with special characters (e.g. `*`, `?`, `|`) in `file_id`.
    *   *Target Route/Behavior*: `GET /api/files/file*name`. Assert status code 400 or proper sanitation.

---

### Tier 3: Pairwise Combinations (8 Tests)

*   **TC-3.1 (Parse -> Download -> Poll)**: Parse a public video URL, retrieve format details, create a download task using the parsed ID, and immediately poll status.
    *   *Target Route/Behavior*: Chain `POST /api/parse`, `POST /api/tasks/download`, and `GET /api/tasks/{task_id}`. Assert the flow operates correctly in sequence.
*   **TC-3.2 (Parse -> Download -> Poll -> Serve)**: Perform parsing, initiate downloading, poll status until progress is 100%, and then retrieve the processed file from the file serving route.
    *   *Target Route/Behavior*: Run full flow: parse URL, create download, mock/wait for 100% completion, request `GET /api/files/{task_id}`.
*   **TC-3.3 (Upload -> Watermark -> Poll -> Serve)**: Upload a watermark mask and image, create a watermark removal task, poll status to success, and fetch the final image from the secure file serving endpoint.
    *   *Target Route/Behavior*: Chain multipart task creation, status polling to success, and retrieval of target file.
*   **TC-3.4 (Watermark -> Poll -> History)**: Create a watermark removal task, poll status, and verify that a new history item appears with matching task ID and type "视频去水印".
    *   *Target Route/Behavior*: Chain watermark creation, poll status, and query `GET /api/history` to ensure the item appears in the history log.
*   **TC-3.5 (Download -> Poll -> History)**: Create a download task, poll status, and verify that a new history item appears with matching task ID and type "视频下载".
    *   *Target Route/Behavior*: Chain download creation, poll status, and query `GET /api/history` to confirm presence.
*   **TC-3.6 (Parse -> Quota Deduction -> Exhaustion)**: Parse a video, create a download task which deducts quota, repeat until quota is exhausted, and verify that subsequent download tasks are rejected.
    *   *Target Route/Behavior*: Execute 5 download actions in sequence under demo user quota constraints, verify the 6th returns 403 / quota limit exceeded.
*   **TC-3.7 (Upload -> Watermark -> Fail -> Poll Error)**: Upload a corrupted video file, trigger watermark processing, let it fail, and verify that status polling returns status "failed" and has a valid error message.
    *   *Target Route/Behavior*: Trigger task setup with simulated error conditions, call status polling, assert `status == "failed"` and `error` field matches.
*   **TC-3.8 (History -> Download Re-run)**: Query history, pick a previous successful task, and re-trigger a download task using its parse ID and format ID, verifying it completes successfully.
    *   *Target Route/Behavior*: Run `GET /api/history`, extract parsed/format data, call `POST /api/tasks/download` and verify duplicate task instantiation or successful execution.

---

### Tier 4: Real-world application scenarios (5 Tests)

*   **TC-4.1 (Regular Video Download Flow)**: A regular user logs in, pastes a Bilibili link, parses it, selects 1080p, starts download, polls progress until success, and downloads the MP4.
    *   *Target Route/Behavior*: Happy path flow mimicking a standard web/miniapp frontend client.
*   **TC-4.2 (Video Watermark Removal Flow)**: A content creator uploads a short video (e.g., 20MB MP4), defines a mask region over the bottom-right watermark, triggers watermark removal in AI mode, polls status until completion, and downloads the repaired video.
    *   *Target Route/Behavior*: Complete upload + mask definition + AI processing mode execution + download.
*   **TC-4.3 (Quota Exhaustion & Recovery Flow)**: A free tier user runs 5 downloads in a row, exhausting their daily limit. The 6th download is blocked. After simulated midnight or manual quota reset, the user is able to parse and download again.
    *   *Target Route/Behavior*: Test temporal/state reset of quota limits.
*   **TC-4.4 (Failed Video Parsing and Support Flow)**: A user tries to parse a private/DRM-protected video. The parsing fails with a clear message indicating DRM protection. The user then submits a feedback message explaining the issue with the URL and their email contact.
    *   *Target Route/Behavior*: Parse failure recovery flow coupled with feedback submission integration.
*   **TC-4.5 (Multi-task Concurrent Operation Flow)**: A user initiates a video download task and concurrently uploads an image to run a watermark removal task. The user queries history to monitor both tasks side by side, verifying their status updates correctly.
    *   *Target Route/Behavior*: Concurrent task instantiation and monitoring via unified history query.

---

## 6. Test Configuration & Execution

### 6.1 Prerequisites
Ensure development and test dependencies are installed:
```bash
pip install -e ".[dev]"
```

### 6.2 Environment Variables for Testing
When running tests, configure the environment to use a dedicated SQLite file and enable/mock AI services:
*   `GREENVIDEO_DATABASE_URL=sqlite:///./storage/test_greenvideo.db`
*   `GREENVIDEO_IOPAINT_ENABLED=true`
*   `GREENVIDEO_IOPAINT_BASE_URL=http://127.0.0.1:8080`

### 6.3 Execution Commands

#### 6.3.1 Pytest Tests
Execute all unit and integration tests inside the `services/api` directory:
```bash
# Navigate to the API service directory
cd services/api

# Run the full test suite
pytest

# Run tests and generate coverage report
pytest --cov=app --cov-report=term-missing
```

#### 6.3.2 Smoke API Test Script
To perform a lightweight end-to-end sanity check against a running server:
1. Start the FastAPI development server:
   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
2. Execute the smoke test script:
   ```bash
   python scripts/smoke_api.py http://127.0.0.1:8000
   ```

#### 6.3.3 Full Feature Integration Script
To execute the comprehensive full feature scenario validation script:
```bash
# Seed the demo database first (optional)
python scripts/seed_demo.py

# Run the full features test script
python services/api/test_all_features.py
```
