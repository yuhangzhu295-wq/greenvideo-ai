"""Tests for task confirmation endpoint and waiting_confirmation lifecycle."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import get_task, insert_task
from app.main import app

client = TestClient(app)


def test_task_confirmation_lifecycle_confirm(monkeypatch, tmp_path):
    executed = []

    async def mock_run(t_id, payload, src):
        executed.append((t_id, payload, src))

    monkeypatch.setattr("app.routers.tasks._run_watermark_task", mock_run)

    task_id = "test_confirm_lifecycle_1"
    cand = [
        {
            "box": [10, 20, 100, 30],
            "tight_box": [12, 22, 96, 26],
            "text": "promo_id",
            "score": 0.65,
            "time_s": 1.2,
        }
    ]
    inp = {
        "repair_mode": "opencv",
        "auto_detect": True,
        "source_path": None,
        "unconfirmed_candidates": cand,
        "requires_confirmation": True,
    }
    insert_task({
        "id": task_id,
        "type": "watermark",
        "status": "waiting_confirmation",
        "progress": 16,
        "stage": "等待确认水印区域",
        "input_json": json.dumps(inp),
    })

    # Status check before confirmation
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "waiting_confirmation"
    assert data["requires_confirmation"] is True
    assert len(data["unconfirmed_candidates"]) == 1

    # Confirm action
    resp_conf = client.post(
        f"/api/tasks/{task_id}/confirm",
        json={"action": "confirm", "confirmed_indices": [0]},
    )
    assert resp_conf.status_code == 200
    data_conf = resp_conf.json()
    assert data_conf["status"] == "processing"
    assert data_conf["requires_confirmation"] is False

    # Verify task state in db
    t = get_task(task_id)
    assert t.status == "processing"
    inp_db = json.loads(t.input_json)
    assert inp_db["requires_confirmation"] is False


def test_task_confirmation_lifecycle_reject(tmp_path):
    task_id = "test_confirm_lifecycle_reject"
    inp = {"unconfirmed_candidates": [], "requires_confirmation": True}
    insert_task({
        "id": task_id,
        "type": "watermark",
        "status": "waiting_confirmation",
        "progress": 16,
        "stage": "等待确认水印区域",
        "input_json": json.dumps(inp),
    })

    resp_rej = client.post(
        f"/api/tasks/{task_id}/confirm",
        json={"action": "reject"},
    )
    assert resp_rej.status_code == 200
    assert resp_rej.json()["status"] == "canceled"

    t = get_task(task_id)
    assert t.status == "canceled"


def test_task_confirmation_invalid_state():
    task_id = "test_confirm_invalid"
    insert_task({
        "id": task_id,
        "type": "watermark",
        "status": "processing",
        "progress": 50,
        "stage": "处理中",
        "input_json": "{}",
    })

    resp = client.post(
        f"/api/tasks/{task_id}/confirm",
        json={"action": "confirm"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_STATE"


def test_task_confirmation_manual_override(monkeypatch):
    executed = []

    async def mock_run(t_id, payload, src):
        executed.append((t_id, payload, src))

    monkeypatch.setattr("app.routers.tasks._run_watermark_task", mock_run)

    task_id = "test_confirm_manual"
    insert_task({
        "id": task_id,
        "type": "watermark",
        "status": "waiting_confirmation",
        "progress": 16,
        "stage": "等待确认水印区域",
        "input_json": json.dumps({"unconfirmed_candidates": [], "requires_confirmation": True}),
    })

    resp = client.post(
        f"/api/tasks/{task_id}/confirm",
        json={
            "action": "manual",
            "custom_regions": [{"x": 10, "y": 10, "w": 50, "h": 50}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    assert len(executed) == 1
    t_id, pl, src = executed[0]
    assert len(pl.mask_regions) == 1
    assert pl.mask_regions[0].x == 10

