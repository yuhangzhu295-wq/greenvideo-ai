from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
LOCAL_MATERIAL = Path(r"C:\Users\zyu33\Desktop\f553635d2972ffa3bca4b94d7ad10b42.mp4")
PROJECT_MATERIAL = Path(__file__).resolve().parent / "fixtures" / "smoke_watermark.mp4"
ARTIFACTS = Path("artifacts")


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, shell=False)


def make_demo_video(path: Path) -> tuple[Path, bool, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        result = run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "10",
                "-vf",
                "drawbox=x=0:y=0:w=iw*0.42:h=ih*0.18:color=black@0.55:t=fill,"
                "drawbox=x=iw*0.60:y=ih*0.78:w=iw*0.40:h=ih*0.18:color=black@0.55:t=fill",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(path),
            ],
            timeout=60,
        )
        if result.returncode == 0 and path.exists():
            return path, True, "本地素材不存在，已使用 demo video fallback。"
    path.write_bytes(b"GreenVideo AI demo video fallback")
    return path, True, "本地素材不存在，FFmpeg 不可用或生成失败，已使用二进制 fallback。"


def ensure_material() -> tuple[Path, bool, str]:
    if LOCAL_MATERIAL.exists():
        return LOCAL_MATERIAL, False, "使用用户提供的本地素材。"
    if PROJECT_MATERIAL.exists():
        return PROJECT_MATERIAL, True, "本地 Desktop 素材不存在，已改用仓库内置测试素材（含可见水印）。"
    return make_demo_video(ARTIFACTS / "demo_watermarked.mp4")


def multipart_body(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----GreenVideo{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def request_json(method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    req = Request(f"{BASE_URL}{path}", data=body, method=method, headers=headers or {})
    with urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_task(task_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = request_json("GET", f"/api/tasks/{task_id}")
        if last["status"] in {"completed", "success", "failed", "canceled"}:
            return last
        time.sleep(1)
    raise RuntimeError(f"Task timeout: {last}")


def download_file(path: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(f"{BASE_URL}{path}", timeout=120) as response:
        target.write_bytes(response.read())


def ffprobe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe") or not path.exists():
        return None
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def extract_frames(input_path: Path, output_path: Path, last: bool = False) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    args = ["ffmpeg", "-y"]
    if last:
        args.extend(["-sseof", "-1"])
    else:
        args.extend(["-ss", "1"])
    args.extend(["-i", str(input_path), "-frames:v", "1", str(output_path)])
    result = run(args, timeout=60)
    return result.returncode == 0 and output_path.exists()


def mask_region_delta(before_path: Path, after_path: Path) -> dict[str, float] | None:
    """Mean absolute pixel delta inside each watermark mask region.

    A real repair must change the masked area; a copied file yields ~0.0.
    """
    if not (before_path.exists() and after_path.exists()):
        return None
    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    before = cv2.imread(str(before_path))
    after = cv2.imread(str(after_path))
    if before is None or after is None or before.shape != after.shape:
        return None
    height, width = before.shape[:2]
    regions = {
        "topleft": (0, 0, int(width * 0.42), int(height * 0.18)),
        "bottomright": (int(width * 0.60), int(height * 0.78), int(width * 0.40), int(height * 0.18)),
    }
    deltas: dict[str, float] = {}
    for name, (x, y, w, h) in regions.items():
        x2, y2 = min(width, x + w), min(height, y + h)
        if x2 <= x or y2 <= y:
            continue
        delta = np.abs(before[y:y2, x:x2].astype(int) - after[y:y2, x:x2].astype(int))
        deltas[name] = round(float(delta.mean()), 3)
    deltas["whole_frame"] = round(
        float(np.abs(before.astype(int) - after.astype(int)).mean()), 3
    )
    return deltas


def main() -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    source, used_demo, material_note = ensure_material()
    fields = {
        "mask_preset": "douyin_both_corners",
        "repair_mode": "opencv",
        "remove_subtitle": "false",
        "confirm_authorized": "true",
    }
    body, boundary = multipart_body(fields, source)
    created = request_json(
        "POST",
        "/api/tasks/watermark",
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    status = wait_task(created["task_id"])
    if status["status"] not in {"completed", "success"}:
        raise SystemExit(f"Watermark task failed: {status}")

    output = ARTIFACTS / "watermark_output.mp4"
    download_file(status["download_url"], output)

    before_duration = ffprobe_duration(source)
    after_duration = ffprobe_duration(output)
    duration_ok = (
        before_duration is None
        or after_duration is None
        or abs(before_duration - after_duration) <= 1.0
    )

    before_001 = extract_frames(source, ARTIFACTS / "watermark_before_001.jpg")
    after_001 = extract_frames(output, ARTIFACTS / "watermark_after_001.jpg")
    before_last = extract_frames(source, ARTIFACTS / "watermark_before_last.jpg", last=True)
    after_last = extract_frames(output, ARTIFACTS / "watermark_after_last.jpg", last=True)

    delta_first = mask_region_delta(
        ARTIFACTS / "watermark_before_001.jpg", ARTIFACTS / "watermark_after_001.jpg"
    )
    delta_last = mask_region_delta(
        ARTIFACTS / "watermark_before_last.jpg", ARTIFACTS / "watermark_after_last.jpg"
    )
    # A genuine repair must move pixels inside at least one watermark mask region.
    repair_happened = any(
        delta and max(
            value for key, value in delta.items() if key != "whole_frame"
        ) > 1.0
        for delta in (delta_first, delta_last)
    )

    def render_delta(delta: dict[str, float] | None) -> str:
        if not delta:
            return "`unavailable`"
        return ", ".join(f"{key}=`{value}`" for key, value in delta.items())

    report = ARTIFACTS / "watermark_validation_report.md"
    report.write_text(
        "\n".join(
            [
                "# Watermark Validation Report",
                "",
                f"- Base URL: `{BASE_URL}`",
                f"- Source: `{source}`",
                f"- 使用 demo fallback: `{used_demo}`",
                f"- Material note: {material_note}",
                f"- Task ID: `{created['task_id']}`",
                f"- Status: `{status['status']}`",
                f"- Output: `{output}`",
                f"- Before duration: `{before_duration}`",
                f"- After duration: `{after_duration}`",
                f"- Duration check <= 1s: `{duration_ok}`",
                f"- Extracted before first frame: `{before_001}`",
                f"- Extracted after first frame: `{after_001}`",
                f"- Extracted before last frame: `{before_last}`",
                f"- Extracted after last frame: `{after_last}`",
                f"- Mask region delta (first frame): {render_delta(delta_first)}",
                f"- Mask region delta (last frame): {render_delta(delta_last)}",
                f"- Repair actually changed masked pixels: `{repair_happened}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(report.read_text(encoding="utf-8"))
    if not output.exists() or output.stat().st_size == 0 or not duration_ok:
        raise SystemExit("Local material verification failed.")
    if not repair_happened:
        raise SystemExit(
            "Mask regions were not modified: the output is not a real watermark repair."
        )


if __name__ == "__main__":
    main()
