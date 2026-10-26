from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def wait_for_task(task_id: str, timeout: float = 4.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"completed", "success", "failed", "canceled"}:
            return last
        time.sleep(0.1)
    return last


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["service"] == "greenvideo-api"
    assert data["storage_ok"] is True


def test_parse_demo_share_text() -> None:
    response = client.post("/api/parse", json={"text": "演示 https://example.com/demo"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "解析完成"
    assert len(data["formats"]) == 3


def test_download_task_fails_instead_of_serving_demo_file() -> None:
    """Real platform download is not implemented, so the task must not pretend.

    The previous behaviour wrote a text file named .mp4 and marked the task
    completed, which handed the user an unplayable file with a success status.
    """
    parsed = client.post("/api/parse", json={"url": "https://example.com/demo"}).json()
    response = client.post(
        "/api/tasks/download",
        json={"parse_id": parsed["id"], "format_id": "1080p"},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "failed"
    assert status["download_url"] is None
    assert status["error"]
    file_response = client.get(f"/api/files/{task_id}")
    assert file_response.status_code == 409


def test_watermark_requires_authorization() -> None:
    response = client.post("/api/tasks/watermark", json={"source_task_id": "task_demo"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AUTHORIZATION_REQUIRED"


def test_watermark_json_task_completes() -> None:
    # The payload must reference a real uploaded file. A source_task_id that
    # does not resolve to media used to fall through to a demo text file that
    # was reported as a successful repair.
    response = client.post(
        "/api/tasks/watermark",
        json={
            "source_task_id": "task_missing",
            "mask_preset": "douyin_both_corners",
            "repair_mode": "opencv",
            "remove_subtitle": False,
            "confirm_authorized": True,
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "failed"
    assert status["download_url"] is None
    assert status["error"]


def test_watermark_unresolvable_source_fails() -> None:
    """A task without usable media must never be reported as completed."""
    response = client.post(
        "/api/tasks/watermark",
        json={"video_url": "https://example.com/missing.mp4", "confirm_authorized": True},
    )
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "failed"
    assert "素材" in (status["stage"] or "")


def test_watermark_multipart_upload() -> None:
    # Valid PNG fixture (120x80 with a bright block in the top-left watermark area).
    # The previous inline fixture was a corrupt 65-byte PNG that no decoder can read,
    # which made the task silently copy the input and report success.
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAHgAAABQCAIAAABd+SbeAAABZElEQVR4Ae3BsREAMAwCMZjc"
        "783TZgEK3yEZUOUZUOUZUOUZUOUZUOUZ0GdmdMHu6hQD+syMLthdnWJAn5nRBburUwzoMzO6"
        "YHd1igF9ZkYX7K5OMaDPzOiC3dUpBvSZGV2wuzrFgD4zowt2V6cY0GdmdMHu6hQD+syMLthd"
        "nWJAn5nRBburUwzoMzO6YHd1igF9ZkYX7K5OMaDPzOiC3dUpBvSZGV2wuzrFgD4zowt2V6cY"
        "UOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZ"
        "UOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZ"
        "UOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZUOUZ"
        "UOUZUOUZUOUZUOUZUOU9K4moUSeXQOoAAAAASUVORK5CYII="
    )
    response = client.post(
        "/api/tasks/watermark",
        data={
            "mask_preset": "douyin_top_left",
            "repair_mode": "opencv",
            "confirm_authorized": "true",
        },
        files={"file": ("test.png", png, "image/png")},
    )
    assert response.status_code == 200
    status = wait_for_task(response.json()["task_id"])
    assert status["status"] == "completed"


def test_history_filter_delete_and_feedback() -> None:
    history = client.get("/api/history?type=all")
    assert history.status_code == 200
    assert len(history.json()["items"]) >= 1

    feedback = client.post(
        "/api/feedback",
        json={"category": "使用建议", "description": "希望增加更多状态提示", "contact": ""},
    )
    assert feedback.status_code == 200
    assert feedback.json()["ok"] is True

    first_id = history.json()["items"][0]["id"]
    deleted = client.delete(f"/api/history/{first_id}")
    assert deleted.status_code == 200


def test_file_path_traversal_protection() -> None:
    response = client.get("/api/files/%2e%2e%2fetc%2fpasswd")
    assert response.status_code in {400, 404}
