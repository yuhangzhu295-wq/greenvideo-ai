# GreenVideo AI Testing Infrastructure Specification & Draft

This document outlines the testing philosophy, infrastructure design, mocking strategy, and execution guidelines for the GreenVideo AI project.

---

## 1. Testing Philosophy

To ensure reliability, security, and smooth user experiences across platforms, the GreenVideo AI project follows a strict testing philosophy consisting of:

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
[ Tier 4: Real-World Scenarios ]
               ▲
[ Tier 3: Cross-Feature Combinations ]
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

## 4. Test Configuration & Execution

### 4.1 Prerequisites
Ensure development dependencies are installed:
```bash
pip install -e ".[dev]"
```

### 4.2 Running Tests
Execute the entire test suite with pytest:
```bash
# Run all tests
pytest

# Run tests and generate coverage report
pytest --cov=app --cov-report=term-missing
```

### 4.3 Environment Variables for Testing
When running tests, the following variables are configured via `pytest.ini` or environment injection:
*   `GREENVIDEO_DATABASE_URL=sqlite:///./storage/test_greenvideo.db`
*   `GREENVIDEO_IOPAINT_ENABLED=true`
*   `GREENVIDEO_IOPAINT_BASE_URL=http://127.0.0.1:8080`
