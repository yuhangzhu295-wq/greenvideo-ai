# Original User Request

## Initial Request — 2026-06-28T06:36:47Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview

Convert the existing static "shell" of the GreenVideo AI project into a fully functional, end-to-end verifiable application with real API bindings, adhering strictly to compliance boundaries.

Working directory: C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai
Integrity mode: development

## Requirements

### R1. Complete Frontend-Backend Binding
- Bind all frontend buttons, forms, and pages in `apps/miniapp` to real API endpoints. No empty functions or `console.log` only handlers.
- Ensure the Taro frontend correctly handles real API responses for parse, download, watermark tasks, history, and feedback.
- Use mock data (`DEMO_MODE`) only as an explicit fallback when backend services are unavailable, and display a clear "演示模式" UI indicator.

### R2. Backend API Implementation
- Ensure the FastAPI backend implements all required endpoints (`/api/health`, `/api/parse`, `/api/tasks/download`, `/api/tasks/watermark`, `/api/tasks/{task_id}`, `/api/history`, `/api/feedback`, `/api/files/{file_id}`) following the exact specifications provided.
- Implement SQLite database persistence using SQLModel/SQLAlchemy for users, parse_results, tasks, history_items, and feedbacks.
- Secure operations: Validate public URLs (prevent SSRF), prevent path traversal for file downloads, and properly handle unsupported formats/protected content.

### R3. Media Processing Integrations
- Integrate `yt-dlp` for parsing public video metadata and downloading within authorized boundaries (no DRM, no login bypass).
- Integrate `ffmpeg` for audio extraction and video frame manipulation.
- Integrate `OpenCV` (default) for watermark removal via inpainting. IOPaint/PaddleOCR remain optional.
- Ensure graceful fallbacks if CLI tools (`yt-dlp`, `ffmpeg`) are not installed on the host machine.

### R4. Compliance and Documentation
- Generate `AUDIT_REPORT.md` (audit of current static elements), `FIX_LOG.md` (record of iterative fixes), `FINAL_REPORT.md`, and `THIRD_PARTY_NOTICES.md` (license verification).
- Strictly enforce compliance: no proxy pools, no cookie scraping, no DRM circumvention. Fail gracefully with business-safe errors.

## Acceptance Criteria

### Verification and Testing
- [ ] `scripts/verify.sh` executes successfully, encompassing: frontend lint/typecheck/build, backend pytest/ruff, Celery import check, and integration checks.
- [ ] `scripts/smoke_api.py` correctly verifies all backend API endpoints.
- [ ] All 10 pages have been verified to execute real logic (e.g., download button creates a real task in DB, processing page polls real progress, feedback saves to DB).
- [ ] No placeholder mock logic is executed unless explicitly running under `DEMO_MODE=true` or missing host dependencies.
- [ ] No GPL-only ffmpeg filters are used; IOPaint model weights are not committed to the repository.

## Follow-up — 2026-06-28T09:46:00Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview

Diagnose and fix the ongoing `(0, d.L) is not a function` bug in the greenvideo-ai miniapp, ensure the URL extraction and video parsing work end-to-end, and provide automated test results as feedback.

Working directory: C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai
Integrity mode: demo

## Requirements

### R1. Fix Frontend Taro Compilation Artifact
Ensure that the frontend code does not trigger the `(0, d.L) is not a function` error when making API requests or using local storage. 

### R2. End-to-End Backend URL Parsing
Ensure the backend API (`/api/parse`) correctly accepts a full paste string (e.g., `4.17 复制打开抖音... https://v.douyin.com/...`) and robustly extracts the actual URL before parsing.

### R3. Provide Clear Restart Instructions
Provide the user with concrete instructions on how to clear the local Taro dev cache and restart the `dev:weapp` process to ensure the newly compiled bundle is used.

### R4. Ensure Open-Source Scraper (`yt-dlp`) is Installed
The backend parsing logic depends on `yt-dlp` being available in the system PATH. Verify if `yt-dlp` is installed, and if not, instruct the user on how to install it (e.g., via `pip install yt-dlp` or downloading the executable), so that the backend does not silently fall back to mock data.

## Acceptance Criteria

### Backend Verification
- [ ] An automated Python script or curl command can successfully send the full string `4.17 复制打开抖音，看看【田宇轩的作品】事实证明158还是能有爱情的吗？ # 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm` to the `/api/parse` endpoint and receive a valid JSON response with the video metadata, without throwing a 400 SecurityError.

### Frontend Instructions
- [ ] A clear instruction is provided to the user detailing how to kill the old Node.js process and restart the WeChat Miniprogram dev server with caching disabled, to ensure the frontend fix is applied.

### Open-Source Dependency Verification
- [ ] A clear instruction or script is provided to verify and install `yt-dlp` locally so that real video scraping works successfully instead of hitting the mock fallback.
