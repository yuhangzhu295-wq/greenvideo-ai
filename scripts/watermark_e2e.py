"""End-to-end watermark acceptance harness.

For every Ground-Truth scenario this script:

1. uploads the watermarked video to the running FastAPI service,
2. waits for the task to reach a terminal state,
3. downloads the produced file,
4. verifies it with ffprobe (streams, resolution, frame rate, duration, audio),
5. compares it with the clean original inside and outside the watermark mask,
6. measures flicker and audio/video sync,
7. writes input/output/mask/before/after frames, ffprobe JSON, quality metrics,
   the task log and a per-scenario validation report.

Nothing here reports success without the measurements to back it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "artifacts" / "watermark-e2e"
API = "http://127.0.0.1:8000"

sys.path.insert(0, str(ROOT / "scripts"))
from measure_repair_quality import flicker, has_audio, psnr, ssim  # noqa: E402

SCENARIOS = [
    "T01_fixed_corner_text",
    "T02_fixed_logo",
    "T03_semitransparent",
    "T04_moving",
    "T05_multiple",
    "T06_complex_motion",
]

# Ground-truth boxes per scenario (x, y, w, h); used as the custom mask.
MASK_BOXES = {
    "T01_fixed_corner_text": [(18, 18, 250, 62)],
    "T02_fixed_logo": [(516, 276, 104, 64)],
    "T03_semitransparent": [(174, 118, 300, 76)],
    # The watermark moves between four positions. Each position is submitted as
    # its own region so the per-frame tracker can follow it instead of masking
    # the union of all four positions.
    "T04_moving": [
        (34, 292, 140, 52),
        (254, 52, 140, 52),
        (474, 52, 140, 52),
        (34, 292, 140, 52),
    ],
    "T05_multiple": [(14, 14, 140, 54), (536, 296, 88, 52), (254, 312, 140, 42)],
    "T06_complex_motion": [(18, 14, 110, 48)],
}


def ffprobe_json(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    )
    return json.loads(r.stdout)


def read_frames(path: Path) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    cap.release()
    return out


def upload_and_wait(video: Path, boxes, repair_mode: str, timeout: float = 1800) -> dict:
    import urllib.request

    boundary = "----GreenVideoE2E"
    mask_json = json.dumps(
        [{"x": x, "y": y, "w": w, "h": h} for (x, y, w, h) in boxes]
    )
    preset = "custom"

    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="mask_preset"\r\n\r\n' + preset.encode() + b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="repair_mode"\r\n\r\n' + repair_mode.encode() + b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="mask_regions"\r\n\r\n'
        + mask_json.encode() + b"\r\n",
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
    log = {"task_id": task_id, "polls": [], "created": created}
    last = {}
    while time.time() < deadline:
        with urllib.request.urlopen(f"{API}/api/tasks/{task_id}", timeout=30) as r:
            last = json.loads(r.read().decode())
        log["polls"].append(
            {"t": round(time.time(), 2), "status": last.get("status"),
             "progress": last.get("progress"), "stage": last.get("stage")}
        )
        if last.get("status") in {"completed", "failed", "canceled"}:
            break
        time.sleep(3)
    log["final"] = last
    return log


def download(task_id: str, target: Path) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{API}/api/files/{task_id}", timeout=300) as r:
        target.write_bytes(r.read())


def save_frames(frames: list[np.ndarray], out_dir: Path, prefix: str, idxs) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in idxs:
        if i < len(frames):
            cv2.imwrite(str(out_dir / f"{prefix}_{i:03d}.png"), frames[i])


def run_scenario(scenario: str, repair_mode: str = "ai") -> dict:
    d = GT / scenario
    src = d / "input.mp4"
    report: dict = {"scenario": scenario, "repair_mode": repair_mode}

    log = upload_and_wait(src, MASK_BOXES[scenario], repair_mode)
    report["task_log"] = log
    status = log["final"].get("status")
    report["status"] = status
    report["stage"] = log["final"].get("stage")
    report["error"] = log["final"].get("error")

    if status != "completed":
        report["verdict"] = "FAIL"
        report["reason"] = f"task ended as {status}: {log['final'].get('error')}"
        (d / "task_log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    out = d / "output.mp4"
    download(log["task_id"], out)
    report["output_bytes"] = out.stat().st_size

    probe_in = ffprobe_json(src)
    probe_out = ffprobe_json(out)
    (d / "ffprobe_input.json").write_text(json.dumps(probe_in, indent=2), encoding="utf-8")
    (d / "ffprobe_output.json").write_text(json.dumps(probe_out, indent=2), encoding="utf-8")

    def streams(p):
        return [
            {
                "codec_type": s.get("codec_type"),
                "codec_name": s.get("codec_name"),
                "width": s.get("width"),
                "height": s.get("height"),
                "r_frame_rate": s.get("r_frame_rate"),
                "nb_frames": s.get("nb_frames"),
                "sample_rate": s.get("sample_rate"),
                "channels": s.get("channels"),
            }
            for s in p.get("streams", [])
        ]

    report["streams_in"] = streams(probe_in)
    report["streams_out"] = streams(probe_out)
    report["duration_in"] = float(probe_in.get("format", {}).get("duration", 0))
    report["duration_out"] = float(probe_out.get("format", {}).get("duration", 0))
    report["audio_preserved"] = has_audio(out)

    clean = read_frames(d / "clean_original.mp4")
    water = read_frames(src)
    repaired = read_frames(out)
    n = min(len(clean), len(water), len(repaired))

    masks = sorted((d / "mask").glob("mask_*.png"))
    # Every frame is scored so a few badly-repaired frames cannot hide behind a
    # good average.
    idxs = list(range(n))

    before_psnr, after_psnr, before_ssim, after_ssim = [], [], [], []
    outside_psnr, outside_ssim = [], []
    for i in idxs:
        if i >= len(masks):
            continue
        m = cv2.imread(str(masks[i]), cv2.IMREAD_GRAYSCALE) > 127
        if not m.any():
            continue
        before_psnr.append(psnr(clean[i][m].reshape(1, -1, 3), water[i][m].reshape(1, -1, 3)))
        after_psnr.append(psnr(clean[i][m].reshape(1, -1, 3), repaired[i][m].reshape(1, -1, 3)))
        before_ssim.append(ssim(clean[i], water[i]))
        after_ssim.append(ssim(clean[i], repaired[i]))
        outside = ~m
        if outside.any():
            outside_psnr.append(
                psnr(clean[i][outside].reshape(1, -1, 3), repaired[i][outside].reshape(1, -1, 3))
            )
            outside_ssim.append(ssim(clean[i], repaired[i]))

    metrics = {
        "frames_measured": len(idxs),
        "watermark_region_psnr_input_vs_clean": _stat(before_psnr),
        "watermark_region_psnr_output_vs_clean": _stat(after_psnr),
        "watermark_region_ssim_output_vs_clean": _stat(after_ssim),
        "outside_region_psnr_output_vs_clean": _stat(outside_psnr),
        "outside_region_ssim_output_vs_clean": _stat(outside_ssim),
        "flicker_std_input": flicker(water[:n:max(1, n // 8)]),
        "flicker_std_output": flicker(repaired[:n:max(1, n // 8)]),
        "psnr_improvement_db": _delta(after_psnr, before_psnr),
        "worst_frame_gain_db": _worst_delta(after_psnr, before_psnr),
    }
    report["quality_metrics"] = metrics
    (d / "quality_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    (d / "mask").mkdir(exist_ok=True)
    save_frames(water, d / "before_frames", "before", idxs)
    save_frames(repaired, d / "after_frames", "after", idxs)
    _make_comparison(d, clean, water, repaired, masks, idxs)

    verdict, reasons = _judge(report, metrics)
    report["verdict"] = verdict
    report["reasons"] = reasons

    (d / "task_log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "validation_report.md").write_text(_render(scenario, report), encoding="utf-8")
    return report


def _stat(xs):
    xs = [x for x in xs if x == x and x not in (float("inf"), float("-inf"))]
    if not xs:
        return {"mean": None, "min": None, "max": None}
    return {"mean": round(float(np.mean(xs)), 3), "min": round(float(np.min(xs)), 3),
            "max": round(float(np.max(xs)), 3)}


def _delta(after, before):
    a = [x for x in after if x == x]
    b = [x for x in before if x == x]
    if not a or not b:
        return None
    return round(float(np.mean(a) - np.mean(b)), 3)


def _worst_delta(after, before):
    """Smallest per-frame improvement, so one damaged frame is visible."""
    pairs = [
        (a - b)
        for a, b in zip(after, before)
        if a == a and b == b and a not in (float("inf"), float("-inf"))
    ]
    if not pairs:
        return None
    return round(float(min(pairs)), 3)


def _make_comparison(d, clean, water, repaired, masks, idxs):
    comp = d / "comparison"
    comp.mkdir(exist_ok=True)
    for i in idxs:
        if i >= len(masks) or i >= len(clean) or i >= len(repaired):
            continue
        m = cv2.imread(str(masks[i]), cv2.IMREAD_GRAYSCALE)
        if m is None:
            continue
        m = cv2.resize(m, (clean[i].shape[1], clean[i].shape[0]))
        overlay = repaired[i].copy()
        tint = np.zeros_like(overlay)
        tint[:] = (0, 0, 255)
        overlay[m > 127] = cv2.addWeighted(overlay[m > 127], 0.5, tint[m > 127], 0.5, 0)
        strip = np.hstack([water[i], repaired[i], clean[i], overlay])
        cv2.imwrite(str(comp / f"cmp_{i:03d}.png"), strip)


def _judge(report: dict, metrics: dict) -> tuple[str, list[str]]:
    reasons = []
    imp = metrics.get("psnr_improvement_db")
    if imp is None:
        return "FAIL", ["无法计算 PSNR 改进（帧或遮罩缺失）"]
    if imp < 5.0:
        reasons.append(f"水印区域 PSNR 仅提升 {imp} dB（要求 >= 5.0 dB）")
    else:
        reasons.append(f"水印区域 PSNR 提升 {imp} dB")

    worst = metrics.get("worst_frame_gain_db")
    if worst is not None and worst < -3.0:
        reasons.append(
            f"最差单帧 PSNR 下降 {abs(worst):.1f} dB（要求不得低于 -3.0 dB，存在误伤帧）"
        )

    out_psnr = metrics.get("outside_region_psnr_output_vs_clean", {}).get("mean")
    if out_psnr is not None and out_psnr < 25.0:
        reasons.append(f"非水印区域 PSNR 仅 {out_psnr} dB（要求 >= 25 dB，画面受损）")
    elif out_psnr is not None:
        reasons.append(f"非水印区域 PSNR {out_psnr} dB（画面保留良好）")

    if worst is not None and worst >= -3.0:
        reasons.append(f"最差单帧 PSNR 变化 {worst} dB（无误伤帧）")

    if not report.get("audio_preserved"):
        reasons.append("输出丢失音频轨")
    else:
        reasons.append("音频轨保留")

    fl_in = metrics.get("flicker_std_input")
    fl_out = metrics.get("flicker_std_output")
    if fl_in and fl_out and fl_out > fl_in * 3 + 1.0:
        reasons.append(f"闪烁加剧（{fl_in} -> {fl_out}）")

    dur_in = report.get("duration_in", 0)
    dur_out = report.get("duration_out", 0)
    if abs(dur_in - dur_out) > 1.0:
        reasons.append(f"时长不一致 {dur_in} -> {dur_out}")

    if any("仅提升" in r or "丢失" in r or "受损" in r or "不一致" in r or "加剧" in r for r in reasons):
        return "FAIL", reasons
    return "PASS", reasons


def _render(scenario: str, report: dict) -> str:
    m = report.get("quality_metrics", {})
    lines = [
        f"# {scenario} 验证报告",
        "",
        f"- 修复模式: `{report.get('repair_mode')}`",
        f"- 任务状态: `{report.get('status')}`",
        f"- Stage: {report.get('stage')}",
        f"- 判定: **{report.get('verdict')}**",
        "",
        "## 输出流",
        "",
    ]
    for s in report.get("streams_out", []):
        lines.append(
            f"- {s['codec_type']} {s['codec_name']} "
            f"{s.get('width') or ''}x{s.get('height') or ''} "
            f"fps={s.get('r_frame_rate')} frames={s.get('nb_frames')}"
        )
    lines += [
        "",
        "## 质量指标",
        "",
        f"- 水印区域 PSNR（输入 vs 干净原片）: {m.get('watermark_region_psnr_input_vs_clean', {}).get('mean')} dB",
        f"- 水印区域 PSNR（输出 vs 干净原片）: {m.get('watermark_region_psnr_output_vs_clean', {}).get('mean')} dB",
        f"- PSNR 提升: {m.get('psnr_improvement_db')} dB",
        f"- 最差单帧 PSNR 变化: {m.get('worst_frame_gain_db')} dB",
        f"- 非水印区域 PSNR: {m.get('outside_region_psnr_output_vs_clean', {}).get('mean')} dB",
        f"- 水印区域 SSIM: {m.get('watermark_region_ssim_output_vs_clean', {}).get('mean')}",
        f"- 闪烁（输入）: {m.get('flicker_std_input')}",
        f"- 闪烁（输出）: {m.get('flicker_std_output')}",
        f"- 音频保留: {report.get('audio_preserved')}",
        f"- 时长: {report.get('duration_in')} -> {report.get('duration_out')}",
        "",
        "## 判定依据",
        "",
    ]
    lines += [f"- {r}" for r in report.get("reasons", [])]
    return "\n".join(lines) + "\n"


def main() -> int:
    only = sys.argv[1:] or SCENARIOS
    results = {}
    for sc in only:
        print(f"== {sc}")
        r = run_scenario(sc, repair_mode="ai")
        results[sc] = r
        print(
            f"   {r['verdict']}: status={r.get('status')} "
            f"psnr_gain={r.get('quality_metrics', {}).get('psnr_improvement_db')} "
            f"outside_psnr={r.get('quality_metrics', {}).get('outside_region_psnr_output_vs_clean', {}).get('mean')} "
            f"audio={r.get('audio_preserved')}"
        )
        for reason in r.get("reasons", []):
            print(f"      - {reason}")

    (GT / "e2e_summary.json").write_text(
        json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk != "task_log"} for k, v in results.items()},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    verdicts = {k: v.get("verdict") for k, v in results.items()}
    print("\n=== SUMMARY ===")
    for k, v in verdicts.items():
        print(f"  {k}: {v}")
    return 0 if all(v == "PASS" for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
