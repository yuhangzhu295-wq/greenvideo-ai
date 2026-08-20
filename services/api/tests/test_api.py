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


def test_download_task_completes_and_serves_file() -> None:
    parsed = client.post("/api/parse", json={"url": "https://example.com/demo"}).json()
    response = client.post(
        "/api/tasks/download",
        json={"parse_id": parsed["id"], "format_id": "1080p"},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "completed"
    assert status["download_url"] == f"/api/files/{task_id}"
    file_response = client.get(status["download_url"])
    assert file_response.status_code == 200
    assert b"GreenVideo AI" in file_response.content


def test_watermark_requires_authorization() -> None:
    response = client.post("/api/tasks/watermark", json={"source_task_id": "task_demo"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AUTHORIZATION_REQUIRED"


def test_watermark_json_task_completes() -> None:
    response = client.post(
        "/api/tasks/watermark",
        json={
            "source_task_id": "task_demo",
            "mask_preset": "douyin_both_corners",
            "repair_mode": "opencv",
            "remove_subtitle": False,
            "confirm_authorized": True,
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "completed"
    assert status["download_url"] == f"/api/files/{task_id}"


def test_watermark_multipart_upload() -> None:
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
        b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
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
