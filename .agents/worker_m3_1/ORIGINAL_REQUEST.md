## 2026-06-28T06:44:56Z
You are teamwork_preview_worker.
Your working directory is: C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\.agents\worker_m3_1
Your parent is: testing_track_orchestrator (Conv ID: feb6eff2-34eb-4c55-afe9-b1f742286b4b)

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task details:
1. Read the global TEST_INFRA.md at the project root (C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\TEST_INFRA.md) to understand the designed test cases (TC-1.1.1 to TC-4.5) for the 8 features.
2. Implement a comprehensive opaque-box pytest integration suite in a new file: C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\services\api\tests\test_integration.py
   - All tests must use FastAPI's TestClient or mock/assert endpoint operations (opaque-box, no internal implementation dependency).
   - Use pytest parameterized tests or individual test functions to implement coverage for all 93 test cases outlined in TEST_INFRA.md. Ensure you hit at least 90+ test cases (make them run cleanly, showing 90+ passed tests).
   - For tests that depend on system binaries (like yt-dlp/ffmpeg) or external APIs (like IOPaint), implement robust monkeypatch mocks to mock `subprocess.run` or relevant services (e.g. media/downloader/watermark services) or use `respx` to mock external endpoints, so that the tests can execute successfully and portably in any environment (including Windows/CI without these binaries installed).
   - For path traversal tests (TC-2.8.1 to TC-2.8.4), make sure they expect 400 Bad Request or raise HTTPException. Note: Since the backend is currently vulnerable, these security tests may fail. That is expected for the E2E Testing Track; do NOT modify application source files. However, you can write them as test cases so that they flag the issues as expected, or you can write assertions that document the expected behavior.
3. Update C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\scripts\smoke_api.py:
   - Expand the main() function to comprehensively check all 8 features in sequence (User/Quota, Parsing, Download, Watermark, Polling, Feedback, History, File serving).
   - Ensure the smoke test runner is robust, accepts BASE_URL as argv, and runs against the live uvicorn server.
4. Verify your work by running pytest and running ruff check inside services/api.
5. When done, write handoff.md in your working directory and notify the parent (ID: feb6eff2-34eb-4c55-afe9-b1f742286b4b) with details of the implementation, files modified/created, and pytest/ruff command outcomes.
