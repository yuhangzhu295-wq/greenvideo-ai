# Handoff Report: GreenVideo AI E2E Test Suite Analysis & Design

## 1. Observation
*   **Path Traversal Vulnerability**: In `services/api/app/routers/files.py` (lines 11-14):
    ```python
    @router.get("/files/{file_id}")
    def get_file(file_id: str) -> FileResponse:
        path = create_demo_file(file_id)
        return FileResponse(path, filename=f"{file_id}.txt")
    ```
    And in `services/api/app/services/storage.py` (lines 21-28):
    ```python
    def create_demo_file(file_id: str) -> Path:
        path = storage_path("files", f"{file_id}.txt")
        if not path.exists():
            path.write_text(
                "GreenVideo AI demo result file.\n真实生产环境会在这里返回签名下载文件。\n",
                encoding="utf-8",
            )
        return path
    ```
    There is no path traversal sanitization on `file_id`.
*   **Database Seeding**: In `services/api/app/db.py` (lines 46-51, 109-116), a `users` table is defined and seeded with a default user `user_demo` with `daily_fast_quota=5`, but there are no API endpoints utilizing this table.
*   **FastAPI & Subprocesses**: Video downloading and watermark tasks write directly to the database with `progress=62` (demo mode). Subprocesses are used in `services/api/app/services/downloader.py` (using `yt-dlp`) and `services/api/app/services/media.py` (using `ffmpeg` / `ffprobe`).
*   **Existing Tests**: Running the existing tests via `.venv/Scripts/pytest` yields:
    ```
    collected 14 items
    tests\test_api.py ....                                                   [ 28%]
    tests\test_security.py ..........                                        [100%]
    ======================== 14 passed, 1 warning in 0.35s ========================
    ```

## 2. Logic Chain
1.  **Vulnerability**: Since `file_id` is joined directly using `settings.storage_dir.joinpath("files", f"{file_id}.txt")`, a user supplying relative directories (e.g. `../../etc/passwd` or `..\..\Windows\win.ini`) can resolve paths outside the `storage/files/` directory.
2.  **Mocking Need**: Since `yt-dlp` and `ffmpeg` system binaries are environment-dependent, running tests on CI/CD runner nodes requires intercepting these commands or checking their availability and mocking the output.
3.  **Authentication Gap**: The database has user tables, but no API authentication routes are mapped. Therefore, user/quota testing must initially verify the database model / seeded data and prepare for mock profile injection.

## 3. Caveats
*   The tests designed for User & Quota Management are based on the database schema and seeded data, since the API does not currently enforce authentication or quota boundaries.
*   Path traversal security tests will fail before applying the safe path resolution patch.

## 4. Conclusion
The backend contains 8 clear features, but is currently running in a simulated "demo mode" where tasks do not invoke celery workers asynchronously. A path traversal bug exists in the files route. A total of 93 test cases have been designed and documented in `analysis.md` across 4 Tiers (Feature Coverage, Boundaries, Cross-Feature, Real-World Scenarios) along with a draft for `TEST_INFRA.md` to guide E2E test implementation.

## 5. Verification Method
1.  To verify the existing test suite:
    ```bash
    cd services/api
    .venv/Scripts/pytest
    ```
2.  To verify files created by this agent:
    *   Inspect `analysis.md` for the list of 93 test cases.
    *   Inspect `TEST_INFRA.md` for test infrastructure specifications.
