"""Submit the real-footage Ground-Truth scenarios as auto_detect tasks and wait.

The eval script (eval_real_footage_e2e.py) measures tasks that are already in
the live database, so this is the submit half of that measurement. Pairing is
done by the SHA-256 of the uploaded file, which is what the eval script uses.

Usage:  python scripts/submit_real_footage_autodetect.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "artifacts" / "real-footage-gt"
API = "http://127.0.0.1:8000"


def upload_and_wait(video: Path, timeout: float = 2400) -> dict:
    boundary = "----GreenVideoRealFootage"
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="auto_detect"\r\n\r\ntrue\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="repair_mode"\r\n\r\nai\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="mask_preset"\r\n\r\ndouyin_both_corners\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="confirm_authorized"\r\n\r\ntrue\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="input.mp4"\r\n',
        b"Content-Type: video/mp4\r\n\r\n",
        video.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{API}/api/tasks/watermark",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        created = json.loads(resp.read().decode())
    task_id = created["task_id"]

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        with urllib.request.urlopen(f"{API}/api/tasks/{task_id}", timeout=30) as r:
            last = json.loads(r.read().decode())
        if last.get("status") in {"completed", "failed", "canceled"}:
            break
        time.sleep(3)
    return {"task_id": task_id, "final": last, "created": created}


def main() -> int:
    scenarios = sorted(p.name for p in GT.iterdir() if p.is_dir())
    summary = {}
    for sc in scenarios:
        src = GT / sc / "input.mp4"
        if not src.exists():
            print(f"{sc}: no input.mp4, skipped")
            continue
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        print(f"{sc}: submitting (sha256 {sha[:12]}...)")
        result = upload_and_wait(src)
        final = result["final"]
        print(f"  -> status={final.get('status')} stage={final.get('stage')} "
              f"error={final.get('error')}")
        summary[sc] = {
            "task_id": result["task_id"],
            "status": final.get("status"),
            "stage": final.get("stage"),
            "error": final.get("error"),
            "input_sha256": sha,
        }
    (GT / "submitted_autodetect.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for v in summary.values() if v["status"] == "completed")
    print(f"\nsubmitted {len(summary)}, completed {ok}")
    return 0 if ok == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
