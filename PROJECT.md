# Project: GreenVideo AI

## Architecture
GreenVideo AI is a video parse, download, and watermark removal application consisting of:
1. **Taro Frontend (`apps/miniapp`)**: A cross-platform WeChat miniapp shell compiled using Taro/React.
2. **FastAPI Backend (`services/api`)**: API gateway providing endpoints for video parsing, task polling, history, and feedback.
3. **SQLite Persistence**: SQLModel-based persistence layer containing users, parse_results, tasks, history_items, and feedback.
4. **Celery Asynchronous Processing**: Background workers processing heavy CLI-bound operations (yt-dlp, ffmpeg) and OpenCV-based image/video processing (watermark removal).

```
[Miniapp Frontend] 
       │ (HTTP REST)
       ▼
[FastAPI Backend] ◄───► [SQLModel SQLite DB]
       │ (Redis Broker)
       ▼
[Celery Background Workers] ◄───► [yt-dlp / ffmpeg / OpenCV]
```

## Code Layout
- `apps/miniapp/src/api/client.ts` - Frontend API client helper.
- `apps/miniapp/src/pages/` - Mini-program pages (faq, feedback, history, home, invite, processing, profile, result, tutorial, watermark).
- `services/api/app/main.py` - FastAPI main entrypoint.
- `services/api/app/db.py` - SQLite persistence (refactored to SQLModel).
- `services/api/app/models.py` - SQLModel data schemas.
- `services/api/app/routers/` - FastAPI router endpoints (parse, tasks, files, feedback, history).
- `services/api/app/services/` - Core business services (downloader, media, security, storage, watermark).
- `services/api/app/workers/` - Celery application and background tasks.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | E2E Test Suite | Design Category-Partition + BVA test cases in scripts/smoke_api.py / pytest | None | IN_PROGRESS |
| M2 | Database SQLModel Refactor | Replace raw sqlite3 with SQLModel/SQLAlchemy persistence in db.py and models.py | None | DONE |
| M3 | Backend Task & Media Integration | Integrate Celery async workers with yt-dlp & ffmpeg; secure /api/files path traversal | M2 | IN_PROGRESS |
| M4 | Frontend API Binding & UI | Connect pages to real API, fix processing polling loop, visual Demo Mode indicator | M3 | PLANNED |
| M5 | E2E Verification & Forensic Audit | Run scripts/verify.sh and run teamwork_preview_auditor | M1, M4 | PLANNED |

## Interface Contracts
### API Endpoint Schemas

1. **POST `/api/parse`**
   - Input: `ParseRequest` { url: string }
   - Output: `ParseResultDTO` { id, title, source, thumbnail, duration, published_at, formats: FormatDTO[] }

2. **POST `/api/tasks/download`**
   - Input: `DownloadTaskRequest` { parse_id, format_id }
   - Output: `TaskCreateResponse` { task_id, status }

3. **POST `/api/tasks/watermark`**
   - Input: multipart form-data or JSON `WatermarkTaskRequest` { source_task_id, mask_regions: MaskRegion[], mode }
   - Output: `TaskCreateResponse` { task_id, status }

4. **GET `/api/tasks/{task_id}`**
   - Output: `TaskStatusDTO` { task_id, type, status, progress, stage, eta_seconds, download_url, error }

5. **GET `/api/files/{file_id}`**
   - Output: file stream response (protected against directory traversal)

6. **GET `/api/history`**
   - Output: `HistoryResponse` { items: HistoryItemDTO[] }

7. **POST `/api/feedback`**
   - Input: `FeedbackRequest` { category, description, contact }
   - Output: `FeedbackResponse` { ok, id }
