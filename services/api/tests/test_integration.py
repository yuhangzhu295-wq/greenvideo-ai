from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_parse_to_download_to_history_flow() -> None:
    parsed = client.post("/api/parse", json={"text": "https://example.com/demo"}).json()
    task = client.post(
        "/api/tasks/download",
        json={"parse_id": parsed["id"], "format_id": parsed["formats"][0]["format_id"]},
    )
    assert task.status_code == 200
    task_id = task.json()["task_id"]

    status = client.get(f"/api/tasks/{task_id}")
    assert status.status_code == 200
    assert status.json()["progress"] >= 0

    history = client.get("/api/history?type=download")
    assert history.status_code == 200
    assert any(item["task_id"] == task_id for item in history.json()["items"])
