from fastapi.testclient import TestClient

from app.main import create_app


def test_root_mount_does_not_shadow_api():
    client = TestClient(create_app())
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/health").status_code == 200

    # If H5 dist is built, root should serve index.html with 200
    res = client.get("/")
    assert res.status_code == 200
    assert "html" in res.headers.get("content-type", "").lower()


def test_upload_cors_not_credentialed_wildcard_conflict():
    client = TestClient(create_app())
    r = client.post(
        "/api/tasks/watermark",
        files={"file": ("a.mp4", b"x")},
        data={"confirm_authorized": "false"},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert r.status_code == 400
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers
