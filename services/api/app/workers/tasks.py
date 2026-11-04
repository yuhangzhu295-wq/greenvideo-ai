from __future__ import annotations

import os

from app.db import update_task
from app.workers.celery_app import celery_app


@celery_app.task(name="greenvideo.demo_process_task")
def demo_process_task(task_id: str) -> dict:
    if os.getenv("GREENVIDEO_DEMO_MODE", "false").lower() != "true":
        update_task(
            task_id,
            status="failed",
            progress=100,
            stage="演示任务已停用",
            error="生产模式下禁止执行无真实产物的 demo 任务。",
        )
        return {"task_id": task_id, "status": "failed"}
    update_task(task_id, status="success", progress=100, stage="处理完成")
    return {"task_id": task_id, "status": "success"}
