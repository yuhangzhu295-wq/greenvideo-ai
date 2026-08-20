# Scope: Gen2 Quick-Fixes

## Architecture
- **Taro Miniapp (`apps/miniapp`)**: React-based mobile mini-program frontend.
- **FastAPI Backend (`services/api`)**: Python backend handling video URL parsing, SQLModel DB, and background processing.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Diagnostics & Exploration | Investigate (0, d.L) bug, URL parsing regex, yt-dlp setup, and tests | None | DONE |
| 2 | Codebase Fixes | Fix frontend Taro API bindings, add @tarojs/router and @types/node, improve backend URL regex | M1 | IN_PROGRESS |
| 3 | Verification & E2E Tests | Run pytest and verify compilation, verify URL parsing and yt-dlp fallback | M2 | PLANNED |
| 4 | Reporting & Handover | Document user instructions for cache clearing and yt-dlp installation | M3 | PLANNED |

## Interface Contracts
- **POST `/api/parse`**: Input `{ url: string }` -> extracts HTTP URL robustly and validates against SSRF.
