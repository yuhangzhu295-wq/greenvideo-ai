# E2E Test Suite Ready

## Test Runner
- Pytest Unit/Integration Suite:
  - Command: `pytest` inside `services/api`
  - Expected: all 107 tests pass (including 93 new E2E/integration tests and 14 baseline tests)
- Live API Smoke Test:
  - Command: `python scripts/smoke_api.py http://127.0.0.1:8000` (while backend server is running on port 8000)
  - Expected: all 8 features pass successfully

## Coverage Summary
| Tier | Count | Description |
|------|------:|-------------|
| 1. Feature Coverage | 40 | Happy path coverage for all 8 features (5 tests per feature) |
| 2. Boundary & Corner | 40 | Edges, limits, validation rules, and security constraints (5 tests per feature) |
| 3. Cross-Feature | 8 | Multi-endpoint transactional combinations |
| 4. Real-World Application | 5 | High-fidelity end-user scenarios |
| **Total** | **93** | Total E2E test cases implemented |

## Feature Checklist
| Feature | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---------|:------:|:------:|:------:|:------:|
| User & Quota Management | 5 | 5 | ✓ | ✓ |
| Video Parsing | 5 | 5 | ✓ | ✓ |
| Video Downloading | 5 | 5 | ✓ | ✓ |
| Watermark/Inpaint Processing | 5 | 5 | ✓ | ✓ |
| Task Status Polling | 5 | 5 | ✓ | ✓ |
| Feedback Submission | 5 | 5 | ✓ | ✓ |
| History Querying | 5 | 5 | ✓ | ✓ |
| Secure File Serving | 5 | 5 | ✓ | ✓ |
