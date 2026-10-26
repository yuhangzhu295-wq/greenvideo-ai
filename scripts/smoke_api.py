from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SMOKE_VIDEO = Path(__file__).resolve().parent / "fixtures" / "smoke_watermark.mp4"


def multipart_request(path: str, fields: dict[str, str], file_path: Path) -> dict:
    """POST a multipart/form-data body carrying a real media file."""
    boundary = f"----GreenVideoSmoke{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = Request(
        f"{BASE_URL}{path}",
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {path} failed: {exc.code} {message}") from exc


def request(method: str, path: str, payload: dict | None = None, raw: bool = False) -> bytes | dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urlopen(req, timeout=15) as response:
            content = response.read()
            if raw:
                return content
            return json.loads(content.decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {message}") from exc


def wait_task(task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = request("GET", f"/api/tasks/{task_id}")  # type: ignore[assignment]
        if last["status"] in {"completed", "success", "failed", "canceled"}:
            return last
        time.sleep(0.4)
    raise RuntimeError(f"Task {task_id} did not finish in time: {last}")


def main() -> None:
    print(f"Starting smoke tests against: {BASE_URL}")

    health = request("GET", "/api/health")
    assert health["ok"] is True
    assert health["storage_ok"] is True
    print("Health check: OK")

    parsed = request("POST", "/api/parse", {"text": "演示链接 https://example.com/demo"})
    assert parsed["id"].startswith("parse_")
    assert len(parsed["formats"]) == 3
    print("Parse demo: OK")

    download = request(
        "POST",
        "/api/tasks/download",
        {"parse_id": parsed["id"], "format_id": parsed["formats"][0]["format_id"]},
    )
    download_status = wait_task(download["task_id"])
    assert download_status["status"] in {"completed", "success"}
    file_bytes = request("GET", download_status["download_url"], raw=True)
    assert b"GreenVideo AI" in file_bytes
    print("Download task: OK")

    # Upload a real decodable video so the watermark task exercises the actual
    # repair engine instead of copying the download task's demo text file.
    if not SMOKE_VIDEO.exists():
        raise RuntimeError(f"Smoke fixture video missing: {SMOKE_VIDEO}")
    watermark = multipart_request(
        "/api/tasks/watermark",
        {
            "mask_preset": "douyin_both_corners",
            "repair_mode": "opencv",
            "remove_subtitle": "false",
            "confirm_authorized": "true",
        },
        SMOKE_VIDEO,
    )
    watermark_status = wait_task(watermark["task_id"], timeout=180.0)
    assert watermark_status["status"] in {"completed", "success"}, watermark_status
    watermark_bytes = request("GET", watermark_status["download_url"], raw=True)
    assert len(watermark_bytes) > 0
    # A real repair must not return the untouched source bytes.
    assert watermark_bytes != SMOKE_VIDEO.read_bytes()
    print("Watermark task: OK")

    feedback = request(
        "POST",
        "/api/feedback",
        {"category": "使用建议", "description": "smoke test feedback", "contact": "smoke@example.com"},
    )
    assert feedback["ok"] is True
    print("Feedback: OK")

    history = request("GET", "/api/history?type=all")
    assert len(history["items"]) >= 1
    print("History: OK")

    print("API smoke tests passed.")


if __name__ == "__main__":
    main()
