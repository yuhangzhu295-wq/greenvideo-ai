from __future__ import annotations

from app.db import update_task
from app.workers.celery_app import celery_app


@celery_app.task(name="greenvideo.demo_process_task")
def demo_process_task(task_id: str) -> dict:
    update_task(task_id, status="success", progress=100, stage="处理完成")
    return {"task_id": task_id, "status": "success"}
