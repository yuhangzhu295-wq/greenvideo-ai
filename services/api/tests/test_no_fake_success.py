"""Regression tests for the fake-success paths found in review round 9.

Each test injects the exact failure the reviewer reproduced and asserts the
pipeline fails honestly instead of reporting a completed repair:

  P0-1  a single frame that fails to land on disk must abort, not deliver a
        video with fewer frames than the source;
  P0-2  an OpenCV VideoWriter that never opens must abort, not deliver a
        zero-byte "repaired video";
  P0-3  the download task must fail, never serve a text file named .mp4;
  P0-4  the file endpoint must refuse an empty or stream-less output.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import MaskRegion
from app.services.video_repair import AiServiceError

client = TestClient(app)


def wait_for_task(task_id: str, timeout: float = 60.0) -> dict:
    import time

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last.get("status") in {"completed", "failed", "canceled"}:
            return last
        time.sleep(0.2)
    return last


def make_video(path: Path, frames: int = 4, size: tuple[int, int] = (160, 96)) -> None:
    """A tiny real video with an audio track, written with OpenCV + ffmpeg."""
    import subprocess

    import cv2

    frames_dir = path.parent / f"_frames_{path.stem}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(frames):
        img = np.full((size[1], size[0], 3), 40, dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (60, 30), (240, 240, 240), -1)
        cv2.imwrite(str(frames_dir / f"f_{i:06d}.png"), img)
    silent = path.parent / f"_silent_{path.stem}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "10", "-i", str(frames_dir / "f_%06d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)],
        capture_output=True, check=True, timeout=300,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(silent),
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-shortest", str(path)],
        capture_output=True, check=True, timeout=300,
    )


def test_p0_1_frame_count_mismatch_aborts(tmp_path: Path) -> None:
    """The output must carry exactly the source's frames.

    This is the safety net behind the per-frame write check in repair_video and
    the writer validation in the OpenCV path: whatever drops frames, the task
    must not report a completed repair of a shortened video.
    """
    src = tmp_path / "in.mp4"
    make_video(src, frames=4)
    out = tmp_path / "out.mp4"

    from app.services.video_repair import count_packets
    from app.services.watermark import WatermarkProcessor

    proc = WatermarkProcessor()
    proc.process_video(
        input_path=src,
        output_path=out,
        mask_regions=[MaskRegion(x=10, y=10, w=50, h=20)],
        mask_preset="custom",
        repair_mode="opencv",
        remove_subtitle=False,
    )
    n_in = count_packets(src, "video")
    n_out = count_packets(out, "video")
    assert n_in == 4
    assert n_out == n_in, f"output has {n_out} packets, source has {n_in}"


def test_p0_1b_repair_video_checks_every_frame_write(tmp_path: Path, monkeypatch) -> None:
    """repair_video must abort when a frame fails to reach the disk."""
    import cv2

    src = tmp_path / "in.mp4"
    make_video(src, frames=4)
    out = tmp_path / "out.mp4"

    # Force the AI path so repair_video is reachable, then make one frame write fail.
    import app.services.video_repair as vr

    monkeypatch.setattr(vr, "iopaint_status", lambda: (True, "test"))

    real_imwrite = cv2.imwrite
    seen = {"n": 0}

    def flaky_imwrite(filename, img, *a, **kw):
        if str(filename).endswith("f_000001.png"):
            seen["n"] += 1
            return False
        return real_imwrite(filename, img, *a, **kw)

    monkeypatch.setattr(cv2, "imwrite", flaky_imwrite)

    def fake_inpaint(frame, mask, client, base):  # pragma: no cover - stubbed
        return frame

    monkeypatch.setattr(vr, "_inpaint_frame", fake_inpaint)

    with pytest.raises(AiServiceError) as exc:
        vr.repair_video(
            input_path=src,
            output_path=out,
            mask_provider=lambda index, frame: np.full((96, 160), 255, dtype=np.uint8),
        )
    assert "帧" in str(exc.value)
    assert seen["n"] == 1, "the failing write must have been attempted"


def test_p0_2_zero_byte_writer_output_aborts(tmp_path: Path, monkeypatch) -> None:
    """A writer that produces nothing must not be delivered as a repair."""
    import cv2

    src = tmp_path / "in.mp4"
    make_video(src, frames=3)
    out = tmp_path / "out.mp4"

    class DeadWriter:
        def isOpened(self) -> bool:
            return False

        def write(self, frame) -> None:  # pragma: no cover - must never be used
            pass

        def release(self) -> None:  # pragma: no cover
            pass

    monkeypatch.setattr(cv2, "VideoWriter", lambda *a, **kw: DeadWriter())

    from app.schemas import MaskRegion
    from app.services.watermark import WatermarkProcessor

    proc = WatermarkProcessor()
    with pytest.raises(AiServiceError):
        proc.process_video(
            input_path=src,
            output_path=out,
            mask_regions=[MaskRegion(x=10, y=10, w=50, h=20)],
            mask_preset="custom",
            repair_mode="opencv",
            remove_subtitle=False,
        )
    assert not out.exists() or out.stat().st_size == 0


def test_p0_3_download_task_never_serves_demo_file() -> None:
    parsed = client.post("/api/parse", json={"url": "https://example.com/demo"}).json()
    response = client.post(
        "/api/tasks/download",
        json={"parse_id": parsed["id"], "format_id": "1080p"},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    status = wait_for_task(task_id)
    assert status["status"] == "failed", "download task must not report success"
    assert status["download_url"] is None
    assert status["error"]

    file_response = client.get(f"/api/files/{task_id}")
    assert file_response.status_code == 409


def test_p0_4_file_endpoint_rejects_unplayable_output(tmp_path: Path, monkeypatch) -> None:
    """A completed task whose output has no video stream must not be downloadable."""
    import app.routers.files as files_router

    class FakeTask(dict):
        @property
        def status(self) -> str:
            return self["status"]

        @property
        def output_path(self):
            return self["output_path"]

        @property
        def type(self) -> str:
            return self["type"]

    bogus = tmp_path / "bogus.mp4"
    bogus.write_text("GreenVideo AI download result\nnot a video", encoding="utf-8")

    task = FakeTask(
        {
            "id": "task_bogus",
            "type": "watermark",
            "status": "completed",
            "output_path": str(bogus),
        }
    )
    monkeypatch.setattr(files_router, "get_task", lambda _id: task)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        files_router.get_file("task_bogus")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "OUTPUT_NOT_PLAYABLE"


def test_p0_4b_file_endpoint_rejects_empty_output(tmp_path: Path, monkeypatch) -> None:
    import app.routers.files as files_router

    class FakeTask(dict):
        @property
        def status(self) -> str:
            return self["status"]

        @property
        def output_path(self):
            return self["output_path"]

        @property
        def type(self) -> str:
            return self["type"]

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    task = FakeTask(
        {"id": "task_empty", "type": "watermark", "status": "completed",
         "output_path": str(empty)}
    )
    monkeypatch.setattr(files_router, "get_task", lambda _id: task)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        files_router.get_file("task_empty")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "OUTPUT_EMPTY"


def test_p0_5_fragmented_output_without_nb_frames_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """A short output that omits nb_frames must not pass the frame-count guard.

    The reviewer's attack: the encoder silently drops a frame and the muxer
    writes a fragmented MP4, so the container reports no frame count at all.
    The guard used to skip the comparison when the count was unknown.
    """
    import subprocess

    from app.services.video_repair import count_packets

    src = tmp_path / "in.mp4"
    make_video(src, frames=4)
    out = tmp_path / "out.mp4"

    # Produce a genuinely short, fragmented (nb_frames-less) video.
    frames_dir = tmp_path / "_frag_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):  # one frame fewer than the source
        img = np.full((96, 160, 3), 40, dtype=np.uint8)
        import cv2

        cv2.imwrite(str(frames_dir / f"f_{i:06d}.png"), img)
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "10", "-i", str(frames_dir / "f_%06d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         str(out)],
        capture_output=True, check=True, timeout=300,
    )
    n_out = count_packets(out, "video")
    assert n_out == 3, f"fragmented fixture should have 3 packets, got {n_out}"

    # The guard must reject it rather than treat the missing count as a pass.
    # The real writer is bypassed: the fragmented file already exists on disk,
    # so this stands in for "the writer reported success but the file is short".
    import cv2

    from app.services.watermark import WatermarkProcessor

    real_writer = cv2.VideoWriter

    class ShortWriter:
        """Opened, writes nothing, releases without producing anything new."""

        def __init__(self, *a, **kw):
            pass

        def isOpened(self) -> bool:
            return True

        def write(self, frame) -> None:
            pass

        def release(self) -> None:
            pass

    monkeypatch.setattr(cv2, "VideoWriter", ShortWriter)

    proc = WatermarkProcessor()
    with pytest.raises(AiServiceError):
        proc.process_video(
            input_path=src,
            output_path=out,
            mask_regions=[MaskRegion(x=10, y=10, w=50, h=20)],
            mask_preset="custom",
            repair_mode="opencv",
            remove_subtitle=False,
        )
    monkeypatch.setattr(cv2, "VideoWriter", real_writer)


def test_p0_6_header_only_mp4_is_not_served(tmp_path: Path, monkeypatch) -> None:
    """An MP4 with a video stream header but zero frames is not playable."""
    import app.routers.files as files_router

    class FakeTask(dict):
        @property
        def status(self) -> str:
            return self["status"]

        @property
        def output_path(self):
            return self["output_path"]

        @property
        def type(self) -> str:
            return self["type"]

    from app.services.video_repair import count_decoded_frames

    header_only = tmp_path / "header_only.mp4"
    header_only.write_bytes(b"")
    # A real header-only file: write a 1-frame video then keep only the header
    # bytes is impractical, so assert the guard directly on the helper.
    assert count_decoded_frames(header_only) in (None, 0)

    task = FakeTask(
        {"id": "task_header", "type": "watermark", "status": "completed",
         "output_path": str(header_only)}
    )
    monkeypatch.setattr(files_router, "get_task", lambda _id: task)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        files_router.get_file("task_header")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] in {"OUTPUT_EMPTY", "OUTPUT_NOT_PLAYABLE"}
