from __future__ import annotations

import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


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

    watermark = request(
        "POST",
        "/api/tasks/watermark",
        {
            "source_task_id": download["task_id"],
            "mask_preset": "douyin_both_corners",
            "repair_mode": "opencv",
            "remove_subtitle": False,
            "confirm_authorized": True,
        },
    )
    watermark_status = wait_task(watermark["task_id"])
    assert watermark_status["status"] in {"completed", "success"}
    assert len(request("GET", watermark_status["download_url"], raw=True)) > 0
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
