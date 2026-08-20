# Scope: Milestone M2 - Database SQLModel Refactor

## Architecture
- Module: services/api
- Database schemas/tables to refactor to SQLModel/SQLAlchemy:
  - users
  - parse_results
  - tasks
  - history_items
  - feedback
- Connection management to refactor from raw sqlite3 to contextual SQLAlchemy/SQLModel session/engine.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M2.1 | Explorer Analysis | Research and design SQLModel schemas and session management (Conv IDs: 3538baa7-d361-4a1f-afce-667f7dc50aa1, 618e19c7-6809-49fc-8dfb-ec63230c09cf, e3777435-1297-40a5-a4d6-7b35f17862da) | None | DONE |
| M2.2 | Worker Implementation | Implement SQLModel schemas, engine, sessions, data seeding, pyproject.toml dependencies | M2.1 | DONE |
| M2.3 | Review & Validation | Review correctness, verify SQLite connections, run pytest suite | M2.2 | DONE |

## Interface Contracts
- Session management: context manager or dependency injection helper for sessions.
- Data structures: SQLModel definitions in services/api/app/models.py or new structures, while preserving backward compatibility with code calling the db methods.
