from __future__ import annotations

import json
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, func, select

from app.config import settings
from app.models import Feedback, HistoryItem, ParseResult, Task, User


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def display_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def ensure_storage() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "files").mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "static" / "demo").mkdir(parents=True, exist_ok=True)


def database_path() -> Path:
    path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


@contextmanager
def get_session() -> Iterable[Session]:
    ensure_storage()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def _add_missing_columns() -> None:
    """Add columns that were introduced after the database was first created.

    SQLModel's create_all only creates missing tables, never alters existing
    ones, so an installed database would keep the old schema and every query
    touching a new column would fail.
    """
    from sqlalchemy import inspect, text

    existing_tables = set(inspect(engine).get_table_names())
    additions = {
        "parse_results": {"url": "TEXT"},
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))


def init_db() -> None:
    ensure_storage()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    seed_demo_data()


DEMO_THUMBNAILS = [
    "linear-gradient(135deg,#7cc9ff,#0f7edb)",
    "linear-gradient(135deg,#ffd4a8,#ff7a59)",
    "linear-gradient(135deg,#203e70,#111827)",
    "linear-gradient(135deg,#d7f0ff,#8b5e34)",
]


def seed_demo_data() -> None:
    with get_session() as session:
        if session.get(User, "user_demo") is None:
            session.add(
                User(
                    id="user_demo",
                    nickname="视频达人",
                    member_level="普通会员",
                    daily_fast_quota=5,
                )
            )

        count = session.exec(select(func.count()).select_from(HistoryItem)).one()
        if count:
            return

        import os

        # Never seed fake history in production or default environments.
        # History must only be populated by genuine user tasks.
        if os.getenv("GREENVIDEO_DEMO_MODE", "false").lower() != "true":
            return

        items = [
            HistoryItem(
                id="hist_aurora",
                task_id=None,
                title="极光之旅的治愈瞬间",
                kind="视频下载",
                thumbnail=DEMO_THUMBNAILS[0],
                status="success",
                size_mb=128.6,
                created_at="2024-05-15 14:32:18",
            ),
            HistoryItem(
                id="hist_sunset",
                task_id=None,
                title="海边日落延时摄影",
                kind="视频去水印",
                thumbnail=DEMO_THUMBNAILS[1],
                status="success",
                size_mb=96.3,
                created_at="2024-05-15 10:21:07",
            ),
            HistoryItem(
                id="hist_city",
                task_id=None,
                title="城市夜景4K素材",
                kind="视频下载",
                thumbnail=DEMO_THUMBNAILS[2],
                status="success",
                size_mb=256.8,
                created_at="2024-05-14 21:45:33",
            ),
            HistoryItem(
                id="hist_coffee",
                task_id=None,
                title="咖啡拉花教学视频",
                kind="视频去水印",
                thumbnail=DEMO_THUMBNAILS[3],
                status="failed",
                size_mb=72.4,
                created_at="2024-05-13 18:09:52",
            ),
        ]
        for item in items:
            session.add(item)


def insert_parse_result(row: dict) -> None:
    parsed = ParseResult(
        id=row["id"],
        url_hash=row["url_hash"],
        url=row.get("url"),
        title=row["title"],
        source=row.get("source", "公开视频"),
        thumbnail=row.get("thumbnail", "/static/demo/thumb.svg"),
        duration=int(row.get("duration", 0) or 0),
        published_at=row.get("published_at", ""),
        formats_json=json.dumps(row.get("formats", []), ensure_ascii=False),
        created_at=utc_now(),
    )
    with get_session() as session:
        session.merge(parsed)


def get_parse_result(parse_id: str) -> ParseResult | None:
    with Session(engine) as session:
        return session.get(ParseResult, parse_id)


def insert_task(row: dict) -> None:
    now = utc_now()
    task = Task(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        progress=row["progress"],
        stage=row["stage"],
        input_json=json.dumps(row.get("input", {}), ensure_ascii=False),
        output_path=row.get("output_path"),
        error=row.get("error"),
        created_at=now,
        updated_at=now,
    )
    with get_session() as session:
        session.add(task)


def update_task(task_id: str, **values: object) -> None:
    if not values:
        return
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return
        # If task was already marked canceled by user, do not allow subsequent
        # background transitions to overwrite status with completed/failed/processing.
        if task.status == "canceled":
            blocked_keys = {"status", "stage", "error", "output_path"}
            values = {k: v for k, v in values.items() if k not in blocked_keys}
            if not values:
                return
        for key, val in values.items():
            if key == "input":
                task.input_json = json.dumps(val, ensure_ascii=False)
            else:
                setattr(task, key, val)
        task.updated_at = utc_now()
        session.add(task)


def get_task(task_id: str) -> Task | None:
    with Session(engine) as session:
        return session.get(Task, task_id)


def fail_stale_running_tasks(reason: str) -> int:
    """Mark tasks stuck in queued/processing from a previous run as failed.

    The backend runs tasks in-process; after a restart nothing resumes them, so
    leaving them queued/processing would hang the user's UI forever. Also
    synchronizes HistoryItem status so the history list doesn't remain 'processing'.
    """
    changed = 0
    with get_session() as session:
        statement = select(Task).where(Task.status.in_(["queued", "processing"]))
        stale_tasks = list(session.exec(statement))
        for task in stale_tasks:
            task.status = "failed"
            task.stage = "服务重启，任务中断"
            task.error = reason
            task.updated_at = utc_now()
            changed += 1

            # Keep HistoryItem synchronized so history cards don't show infinite loading
            hist_statement = select(HistoryItem).where(HistoryItem.task_id == task.id)
            for item in session.exec(hist_statement):
                item.status = "failed"

        # Also fail any orphaned or unlinked processing items to prevent hanging UI
        orphan_statement = select(HistoryItem).where(HistoryItem.status == "processing")
        for item in session.exec(orphan_statement):
            item.status = "failed"
    return changed


def delete_task(task_id: str) -> bool:
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return False
        session.delete(task)
        return True


def insert_history(row: dict) -> None:
    history = HistoryItem(
        id=row["id"],
        task_id=row.get("task_id"),
        title=row["title"],
        kind=row["kind"],
        thumbnail=row["thumbnail"],
        status=row["status"],
        size_mb=row["size_mb"],
        created_at=row["created_at"],
    )
    with get_session() as session:
        session.add(history)


def update_history_by_task(task_id: str, **values: object) -> None:
    if not values:
        return
    with get_session() as session:
        rows = session.exec(select(HistoryItem).where(HistoryItem.task_id == task_id)).all()
        for row in rows:
            for key, val in values.items():
                setattr(row, key, val)
            session.add(row)


def list_history(kind_filter: str = "all", status_filter: str | None = None) -> list[HistoryItem]:
    with Session(engine) as session:
        statement = select(HistoryItem)
        if kind_filter == "download":
            statement = statement.where(HistoryItem.kind == "视频下载")
        elif kind_filter == "watermark":
            statement = statement.where(HistoryItem.kind == "视频去水印")
        elif kind_filter == "processing":
            statement = statement.where(HistoryItem.status == "processing")
        elif kind_filter == "success":
            statement = statement.where(HistoryItem.status == "success")
        elif kind_filter == "failed":
            statement = statement.where(HistoryItem.status == "failed")
        if status_filter:
            statement = statement.where(HistoryItem.status == status_filter)
        statement = statement.order_by(HistoryItem.created_at.desc(), HistoryItem.id.desc())
        return list(session.exec(statement).all())


def delete_history(history_id: str) -> bool:
    with get_session() as session:
        item = session.get(HistoryItem, history_id)
        if item is None:
            return False
        session.delete(item)
        return True


def insert_feedback(row: dict) -> None:
    fb = Feedback(
        id=row["id"],
        category=row["category"],
        description=row["description"],
        contact=row.get("contact", ""),
        created_at=utc_now(),
    )
    with get_session() as session:
        session.add(fb)
