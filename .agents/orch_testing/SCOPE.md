# Scope: E2E Testing Track

## Architecture
- Dual track E2E testing: Opaque-box API testing through scripts/smoke_api.py and pytest integration tests.
- Target endpoints and features:
  - Video parsing (POST /api/parse)
  - Video downloading (POST /api/tasks/download)
  - Watermark/Inpaint processing (POST /api/tasks/watermark)
  - Task status polling (GET /api/tasks/{task_id})
  - User/Quota management (TBD based on API inspection)
  - Feedback submission (POST /api/feedback)
  - History querying (GET /api/history)
  - Secure file serving (GET /api/files/{file_id})

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration | Inspect existing test code and backend routers, prepare TEST_INFRA.md draft | None | PLANNED |
| 2 | Test Design (TEST_INFRA.md) | Write the comprehensive TEST_INFRA.md with 90+ test cases across 4 Tiers | M1 | PLANNED |
| 3 | Implementation (smoke_api.py) | Implement comprehensive E2E tests in scripts/smoke_api.py | M2 | PLANNED |
| 4 | Integration tests (pytest) | Implement pytest integration tests in services/api/tests/test_integration.py | M3 | PLANNED |
| 5 | Review and Auditing | Run reviewers and Forensic Auditor to ensure cleanliness and test coverage | M4 | PLANNED |
| 6 | Test Summary (TEST_READY.md) | Publish TEST_READY.md and handoff report to parent | M5 | PLANNED |

## Interface Contracts
- API routes must be tested as opaque-box via endpoints.
- External dependencies (e.g. download process, video generation) should support simulated or actual execution as appropriate for a smoke test.
