# Scope: Milestone M3 - Backend Task Asynchronous Processing & Media Integration

## Architecture
- Module/package boundaries, data flow, shared interfaces:
  - Celery configuration & background worker setup.
  - Task router: task creation endpoints, triggering backend processing via Celery asynchronously using `.delay()`.
  - Media utils/services: downloading via yt-dlp, audio extraction via ffmpeg, watermark removal via OpenCV/numpy (traditional inpaint) or IOPaint.
  - File router: path traversal protection for serving files.
  - Database status transitions: queued -> processing -> success / failed.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Explore Celery & Media Utils | Explorer analyzes celery configuration, tasks.py, routers/tasks.py, and routers/files.py | None | IN_PROGRESS |
| 2 | Implement Backend & Media Tasks | Worker implements Celery task logic, safe yt-dlp and ffmpeg subprocess commands, watermark service, and secure file serving | 1 | PLANNED |
| 3 | Verify Async Media Backend | Reviewers, Challengers, and Forensic Auditor verify correctness, database status updates, path traversal security, and test suite execution | 2 | PLANNED |

## Interface Contracts
### routers/tasks.py ↔ workers/tasks.py
- Task creation endpoint triggers Celery worker via `.delay()`.
- Task execution updates DB task records dynamically (statuses: queued -> processing -> success / failed).
- `/api/files/{file_id}` only serves files within `uploads` folder and rejects traversal attempts (raising 404).
