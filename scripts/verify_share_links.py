from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
EXPECTED_BLOCKED = {
    "LOGIN_REQUIRED_NOT_SUPPORTED",
    "DRM_NOT_SUPPORTED",
    "PRIVATE_VIDEO_NOT_SUPPORTED",
    "PLATFORM_BLOCKED_OR_RATE_LIMITED",
}

SAMPLES = {
    "douyin": (
        "4.17 复制打开抖音，看看【田宇轩的作品】事实证明是58还是能有爱情的吗？# 158身高 "
        "... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm"
    ),
    "bilibili": "【我给Codex做了一个分镜台，Codex秒变视频分镜脚本工作台】https://b23.tv/UlH6LvD",
    "kuaishou": (
        "https://v.kuaishou.com/KDINf2L9 用最平静的声音说出最绝望的话 “缅甸诈骗电话 ” "
        "毁掉 该作品在快手被播放过1,811万次，点击链接，打开【快手】直接观看！"
    ),
}


def parse_detail(payload: bytes) -> tuple[str, str]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return "INVALID_JSON", payload.decode("utf-8", errors="replace")
    detail = data.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or ""), str(detail.get("message") or "")
    return "UNKNOWN_ERROR", str(detail)


def verify_one(name: str, text: str) -> dict:
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = Request(
        f"{BASE_URL}/api/parse",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {"name": name, "result": "success", "title": data.get("title"), "source": data.get("source")}
    except HTTPError as exc:
        payload = exc.read()
        code, message = parse_detail(payload)
        result = "expected_blocked" if exc.code == 422 and code in EXPECTED_BLOCKED else "failed_bug"
        return {"name": name, "result": result, "status": exc.code, "code": code, "message": message}
    except URLError as exc:
        return {"name": name, "result": "failed_bug", "message": str(exc)}


def main() -> None:
    reports = [verify_one(name, text) for name, text in SAMPLES.items()]
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    report_path = artifacts / "share_links_report.md"
    lines = ["# Share Links Report", "", f"Base URL: `{BASE_URL}`", ""]
    for item in reports:
        lines.append(
            f"- {item['name']}: `{item['result']}`"
            + (f" ({item.get('code')}: {item.get('message')})" if item.get("code") else "")
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path.read_text(encoding="utf-8"))

    failed = [item for item in reports if item["result"] == "failed_bug"]
    if failed:
        raise SystemExit(f"Share link verification failed: {failed}")


if __name__ == "__main__":
    main()
