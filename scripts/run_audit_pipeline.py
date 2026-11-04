"""Runner script to execute full watermark detection, tracking render, and LaMa inpainting
for multi-platform audit videos on RTX 5060 GPU.
"""
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.services.watermark_detect import (
    detect_timed_watermarks_hybrid,
    render_tracking_preview_video,
    verify_watermark_removal,
)
from app.services.watermark import FrameTrajectoryMaskProvider
from app.services.video_repair import repair_video
import cv2

def run_pipeline(video_name: str):
    audit_dir = Path("C:/Users/zyu33/Desktop/public_video_audit")
    input_path = audit_dir / f"test_{video_name}_12s.mp4"
    tracking_path = audit_dir / f"out_{video_name}_tracking.mp4"
    repaired_path = audit_dir / f"out_{video_name}_repaired.mp4"

    print(f"\n=======================================================")
    print(f"Starting pipeline for {video_name}: {input_path}")
    print(f"=======================================================")
    t0 = time.time()

    # 1. Detection & Dense Tracking
    print("[1/4] Running detect_timed_watermarks_hybrid...")
    det_res = detect_timed_watermarks_hybrid(input_path)
    if not det_res.ok or not det_res.frame_tracks:
        print(f"FAILED: detection error: {det_res.reason}")
        return False

    print(f"  -> Detections: {len(det_res.detections)}")
    for idx, d in enumerate(det_res.detections):
        print(f"     [{idx}] box={d.box}, time={d.start_s}s-{d.end_s}s, moving={d.is_moving}")
    print(f"  -> Frame tracks: {len(det_res.frame_tracks)} frames")

    # 2. Render Green Box & Keypoint Tracking Preview Video
    print("[2/4] Rendering tracking preview video...")
    t_track = time.time()
    render_tracking_preview_video(
        input_path,
        det_res.frame_tracks,
        tracking_path,
        max_frames=det_res.outro_cut_frame,
    )
    print(f"  -> Tracking preview rendered in {time.time() - t_track:.1f}s: {tracking_path} ({tracking_path.stat().st_size} bytes)")

    # 3. LaMa Inpainting
    print("[3/4] Running LaMa inpainting with FrameTrajectoryMaskProvider...")
    cap = cv2.VideoCapture(str(input_path))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    provider = FrameTrajectoryMaskProvider(det_res.frame_tracks, h, w)
    t_repair = time.time()
    report = repair_video(
        input_path=input_path,
        output_path=repaired_path,
        mask_provider=provider.mask_for,
        max_frames=det_res.outro_cut_frame,
    )
    print(f"  -> Repaired video rendered in {time.time() - t_repair:.1f}s: {repaired_path} ({repaired_path.stat().st_size} bytes)")
    print(f"     Report: frames_repaired={report.frames_repaired}, skipped={report.frames_skipped}, mask_coverage={report.mask_coverage_pct}%")

    # 4. Post-repair residual verification
    print("[4/4] Verifying watermark removal residual...")
    verify_res = verify_watermark_removal(
        input_path,
        repaired_path,
        det_res.detections,
        fps=fps,
        max_frames=det_res.outro_cut_frame,
        samples_per_det=2,
        match_threshold=0.6,
    )
    print(f"  -> Residual check: {verify_res}")
    total_time = time.time() - t0
    print(f"COMPLETED {video_name} in {total_time:.1f}s! Residual ok: {verify_res.get('ok')}")
    return verify_res.get("ok", False)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "tiktok"
    ok = run_pipeline(target)
    sys.exit(0 if ok else 1)
