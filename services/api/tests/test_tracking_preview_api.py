from fastapi.testclient import TestClient
from app.db import insert_task
from app.main import app
from app.services.storage import storage_path

client = TestClient(app)


def test_get_tracking_preview_file_not_ready():
    insert_task(
        {
            "id": "task_preview_test_1",
            "type": "watermark",
            "status": "processing",
            "progress": 20,
            "stage": "修复中",
            "input": {},
        }
    )
    resp = client.get("/api/files/task_preview_test_1/tracking")
    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"]["code"] == "PREVIEW_NOT_READY"


def test_get_tracking_preview_file_success(tmp_path):
    preview_file = storage_path("files", "tracking_task_preview_test_2.mp4")
    preview_file.write_bytes(b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41")

    insert_task(
        {
            "id": "task_preview_test_2",
            "type": "watermark",
            "status": "processing",
            "progress": 30,
            "stage": "正在智能修复",
            "input": {
                "tracking_preview_ready": True,
                "frame_tracks": [{"frame_index": 0, "timestamp_s": 0.0, "boxes": []}],
                "video_width": 720,
                "video_height": 1280,
            },
        }
    )

    resp = client.get("/api/files/task_preview_test_2/tracking")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"

    status_resp = client.get("/api/tasks/task_preview_test_2")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert (
        status_data["tracking_preview_url"]
        == "/api/files/task_preview_test_2/tracking"
    )
    assert status_data["video_width"] == 720
    assert status_data["video_height"] == 1280
    assert isinstance(status_data["frame_tracks"], list)

    preview_file.unlink(missing_ok=True)


def test_get_tracking_preview_unsafe_path():
    resp = client.get("/api/files/test..id/tracking")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "UNSAFE_PATH"
