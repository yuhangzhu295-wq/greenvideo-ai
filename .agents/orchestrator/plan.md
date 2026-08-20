# Execution Plan

This plan details the step-by-step implementation for GreenVideo AI.

## Step 1: E2E Test Suite Creation (Milestone M1)
- **Agent Role**: E2E Testing Orchestrator
- **Directory**: `.agents/orch_testing`
- **Objective**: Design and build the opaque-box integration/smoke tests in `scripts/smoke_api.py`.
- **Methodology**: Define Category-Partition and BVA tests. At least 5 tests per feature. Integrate into pytest.
- **Verification**: Run `python scripts/smoke_api.py` and output a test summary report. Produce `TEST_READY.md`.

## Step 2: Database Refactor using SQLModel (Milestone M2)
- **Agent Role**: Database Implementation Worker
- **Directory**: `.agents/worker_db`
- **Objective**: Refactor `services/api/app/db.py` and `services/api/app/models.py` to use SQLModel for ORM mapping. Ensure connection lifecycle management and automated schema migration work correctly.
- **Verification**: Run backend pytest database checks and unit tests.

## Step 3: Backend Task Asynchronous Processing (Milestone M3)
- **Agent Role**: Backend Integration Worker
- **Directory**: `.agents/worker_backend`
- **Objective**:
  1. Trigger Celery tasks inside `create_download_task` and `create_watermark_task` endpoints.
  2. Implement actual asynchronous tasks in `services/api/app/workers/tasks.py`.
     - Download task: run `yt-dlp` to download, run `ffmpeg` to process (extract audio if MP3), save output path, update database.
     - Watermark task: run OpenCV or IOPaint inpainting on frame sequence, compile output video, update database.
  3. Secure `/api/files/{file_id}` to prevent path traversal (ensure resolved file is located inside settings.storage_dir and is a valid registered task file).
- **Verification**: Run unit tests, verify Celery worker processes tasks, run smoke tests.

## Step 4: Frontend API Binding & UI Updates (Milestone M4)
- **Agent Role**: Frontend Binding Worker
- **Directory**: `.agents/worker_frontend`
- **Objective**:
  1. Bind API client in `apps/miniapp/src/api/client.ts` to backend when `TARO_APP_API_BASE_URL` is set, showing "演示模式" (Demo Mode) indicator UI only when base URL is missing.
  2. Connect all visual triggers (buttons, forms, FAQ, history list filters, profiles).
  3. Correct processing page polling logic to redirect users to results or history upon success/failure.
  4. Ensure results copy buttons use Taro clipboard API.
- **Verification**: Build the frontend (typecheck, lint, and build) using pnpm.

## Step 5: E2E Integration and Forensic Audit (Milestone M5)
- **Agent Role**: Verification & Forensic Auditor
- **Directory**: `.agents/worker_audit`
- **Objective**:
  1. Execute `scripts/verify.sh` to run tests across front and backend.
  2. Run the `teamwork_preview_auditor` tool to perform Forensic Audit checking for integrity violations.
- **Verification**: Verify that verify.sh finishes successfully and audit verdict is CLEAN.
