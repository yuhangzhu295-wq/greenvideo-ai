"""Download flow tests.

Foreign video hosts are unreliable from this network (intermittent TLS resets
and Cloudflare challenges), so the mechanics are tested against a local HTTP
server. The URL validator is patched only inside these tests; production keeps
the SSRF guard.

What is verified here:
  * a real yt-dlp download produces a playable file that is then moved into the
    task's output path;
  * the task reports completed only when the produced file really decodes;
  * a parse row without a stored URL fails honestly instead of pretending;
  * a download that yields nothing fails instead of serving an empty file.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from app.services import downloader as downloader_module
from app.services.downloader import DownloadError, VideoDownloader


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Create the test database schema for the DB-backed tests below.

    conftest deletes the test database at import time; tests that talk to the
    database directly (rather than through TestClient, which runs startup) must
    create it themselves.
    """
    from app.db import init_db

    init_db()
    yield


@pytest.fixture()
def video_server(tmp_path: Path):
    """Serve one real MP4 over HTTP so yt-dlp has a genuine URL to fetch."""
    media = tmp_path / "media"
    media.mkdir()
    frames = tmp_path / "_frames"
    frames.mkdir()
    import cv2
    import numpy as np

    for i in range(12):
        img = np.full((120, 160, 3), 30 + i * 5, dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (70, 40), (240, 240, 240), -1)
        cv2.imwrite(str(frames / f"f_{i:06d}.png"), img)
    clip = media / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "10", "-i", str(frames / "f_%06d.png"),
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.2",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip)],
        capture_output=True, check=True, timeout=300,
    )

    port = _free_port()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(media)
    )

    class Quiet(handler.func):  # type: ignore[misc]
        def log_message(self, *args):  # noqa: D401 - silence test server
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port),
        functools.partial(Quiet, directory=str(media)),
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/clip.mp4", clip
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def allow_localhost(monkeypatch):
    """Let the downloader reach the local test server without weakening prod."""
    monkeypatch.setattr(
        downloader_module, "validate_public_http_url", lambda url: url
    )


def test_download_produces_playable_file(video_server, allow_localhost, tmp_path):
    url, _clip = video_server
    out_dir = tmp_path / "out"
    d = VideoDownloader()
    produced = d.download(url, "best", out_dir)

    assert produced.exists()
    assert produced.stat().st_size > 0
    from app.services.video_repair import count_decoded_frames

    decoded = count_decoded_frames(produced)
    assert decoded and decoded > 0, "downloaded file must decode to real frames"


def test_download_without_url_fails_honestly(allow_localhost, tmp_path):
    d = VideoDownloader()
    with pytest.raises(DownloadError) as exc:
        d.download("", "best", tmp_path / "out")
    assert exc.value.code == "PARSE_RESULT_EXPIRED"


def test_download_missing_media_fails_honestly(video_server, allow_localhost, tmp_path):
    url, _clip = video_server
    d = VideoDownloader()
    # A 404 path: yt-dlp must report failure, and no file may be produced.
    missing = url.replace("/clip.mp4", "/nope.mp4")
    out_dir = tmp_path / "out"
    with pytest.raises(DownloadError):
        d.download(missing, "best", out_dir)
    assert not list(out_dir.glob("download.*")) if out_dir.exists() else True


def test_selector_pairs_video_with_audio():
    d = VideoDownloader()
    assert d.build_selector("1080p")[1] is False
    assert "+bestaudio" in d.build_selector("1080p")[0]
    assert d.build_selector("720p")[1] is False
    assert "+bestaudio" in d.build_selector("720p")[0]
    assert d.build_selector("mp3") == ("bestaudio/best", True)
    assert d.build_selector("")[0] == "best"
    sel, audio_only = d.build_selector("137")
    assert sel.startswith("137+bestaudio") and audio_only is False


def test_formats_report_unknown_size_instead_of_inventing_one():
    d = VideoDownloader()
    # No per-format metadata: must not fabricate resolutions or byte sizes.
    formats = d._select_formats([])
    assert all(f.size_mb == 0.0 for f in formats)
    assert [f.format_id for f in formats] == ["best", "mp3"]


def test_formats_use_real_metadata_when_present():
    d = VideoDownloader()
    formats = d._select_formats(
        [
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "height": 1080,
             "filesize": 104857600},
            {"format_id": "136", "ext": "mp4", "vcodec": "avc1", "height": 720,
             "filesize": 52428800},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "aac",
             "filesize": 4194304},
        ]
    )
    ids = [f.format_id for f in formats]
    assert ids == ["137", "136", "140"]
    assert formats[0].size_mb == 100.0
    assert formats[2].ext == "mp3"


def test_find_ytdlp_prefers_interpreter_local():
    d = VideoDownloader()
    found = d._find_ytdlp()
    assert found is not None
    import sys

    local_dir = Path(sys.executable).parent
    assert Path(found).parent == local_dir, (
        "must prefer the pinned interpreter-local yt-dlp over a PATH install"
    )


def test_task_wiring_moves_file_and_marks_completed(monkeypatch, tmp_path):
    """Exercise _run_download_task's wiring with a stubbed downloader."""
    import asyncio

    from app.db import get_task, insert_parse_result, insert_task
    from app.routers import tasks as tasks_module

    pid = "parse_wiring_test"
    insert_parse_result(
        {
            "id": pid,
            "url_hash": "wiringhash",
            "url": "https://example.invalid/video.mp4",
            "title": "wiring",
            "source": "test",
            "thumbnail": "",
            "duration": 1,
            "published_at": "2024-01-01 00:00",
            "formats": [{"format_id": "best", "label": "原始文件", "ext": "mp4", "size_mb": 0.0}],
        }
    )

    tid = "task_wiring_test"
    insert_task(
        {
            "id": tid,
            "type": "download",
            "status": "queued",
            "progress": 0,
            "stage": "等待下载",
            "input": {"parse_id": pid, "format_id": "best"},
        }
    )

    # Produce a real playable file at the path the downloader would return.
    src = tmp_path / "stub.mp4"
    frames = tmp_path / "_f"
    frames.mkdir()
    import cv2
    import numpy as np

    for i in range(6):
        cv2.imwrite(
            str(frames / f"f_{i:06d}.png"),
            np.full((96, 128, 3), 40 + i * 5, dtype=np.uint8),
        )
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "10", "-i", str(frames / "f_%06d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, check=True, timeout=300,
    )

    def fake_download(self, url, format_id, output_dir, progress_cb=None, cancel_check=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / "download.mp4"
        shutil.copyfile(src, dest)
        if progress_cb:
            progress_cb(80, "stub")
        return dest

    monkeypatch.setattr(VideoDownloader, "download", fake_download)

    class Payload:
        parse_id = pid
        format_id = "best"

    asyncio.run(tasks_module._run_download_task(tid, Payload()))

    task = get_task(tid)
    assert task.status == "completed", f"expected completed, got {task.status}: {task.error}"
    out = Path(task.output_path)
    assert out.exists() and out.stat().st_size > 0

    from app.services.video_repair import count_decoded_frames

    assert count_decoded_frames(out) and count_decoded_frames(out) > 0


def test_task_wiring_fails_when_downloader_raises(monkeypatch):
    import asyncio

    from app.db import get_task, insert_parse_result, insert_task
    from app.routers import tasks as tasks_module

    pid = "parse_wiring_fail"
    insert_parse_result(
        {
            "id": pid, "url_hash": "wiringfail", "url": "https://example.invalid/x.mp4",
            "title": "x", "source": "test", "thumbnail": "", "duration": 1,
            "published_at": "2024-01-01 00:00",
            "formats": [{"format_id": "best", "label": "原始文件", "ext": "mp4", "size_mb": 0.0}],
        }
    )
    tid = "task_wiring_fail"
    insert_task(
        {"id": tid, "type": "download", "status": "queued", "progress": 0,
         "stage": "等待下载", "input": {"parse_id": pid, "format_id": "best"}}
    )

    def boom(self, *a, **kw):
        raise DownloadError("LOGIN_REQUIRED_NOT_SUPPORTED", "需要登录，本项目不支持绕过。")

    monkeypatch.setattr(VideoDownloader, "download", boom)

    class Payload:
        parse_id = pid
        format_id = "best"

    asyncio.run(tasks_module._run_download_task(tid, Payload()))
    task = get_task(tid)
    assert task.status == "failed"
    assert "LOGIN_REQUIRED_NOT_SUPPORTED" in (task.error or "")
    assert task.output_path in (None, "")


def test_parse_row_without_url_is_rejected():
    """Rows written before the url column existed must not silently download."""
    from app.db import get_parse_result, insert_parse_result

    pid = "parse_no_url"
    insert_parse_result(
        {
            "id": pid, "url_hash": "nourl", "url": None,
            "title": "legacy", "source": "test", "thumbnail": "", "duration": 0,
            "published_at": "2024-01-01 00:00", "formats": [],
        }
    )
    row = get_parse_result(pid)
    assert row is not None and row.url is None

    d = VideoDownloader()
    with pytest.raises(DownloadError) as exc:
        d.download(row.url or "", "best", Path("unused"))
    assert exc.value.code == "PARSE_RESULT_EXPIRED"


def _make_audio(tmp_path: Path) -> Path:
    mp3 = tmp_path / "audio.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
         "-c:a", "libmp3lame", str(mp3)],
        capture_output=True, check=True, timeout=300,
    )
    return mp3


def test_file_endpoint_serves_audio_only_output(tmp_path, monkeypatch):
    """An mp3 download must be servable; it has no video frames by design."""

    import app.routers.files as files_router

    mp3 = _make_audio(tmp_path)

    class FakeTask(dict):
        @property
        def status(self):
            return self["status"]

        @property
        def output_path(self):
            return self["output_path"]

        @property
        def type(self):
            return self["type"]

    task = FakeTask(
        {"id": "t_audio", "type": "download", "status": "completed",
         "output_path": str(mp3)}
    )
    monkeypatch.setattr(files_router, "get_task", lambda _id: task)
    response = files_router.get_file("t_audio")
    assert Path(response.path) == mp3


def test_file_endpoint_rejects_audio_output_without_track(tmp_path, monkeypatch):
    """An .mp3 that is not actually audio must not be served."""
    from fastapi import HTTPException

    import app.routers.files as files_router

    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio at all" * 10)

    class FakeTask(dict):
        @property
        def status(self):
            return self["status"]

        @property
        def output_path(self):
            return self["output_path"]

        @property
        def type(self):
            return self["type"]

    task = FakeTask(
        {"id": "t_bad_audio", "type": "download", "status": "completed",
         "output_path": str(bogus)}
    )
    monkeypatch.setattr(files_router, "get_task", lambda _id: task)
    with pytest.raises(HTTPException) as exc:
        files_router.get_file("t_bad_audio")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "OUTPUT_NOT_PLAYABLE"


def test_network_failure_is_not_reported_as_parse_failure():
    """A TLS/connection reset must not be described as an unsupported link."""
    d = VideoDownloader()
    err = d._classify_failure(
        "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol",
        "https://example.invalid/v.mp4",
    )
    assert err.code == "NETWORK_ERROR"


def test_protected_content_is_refused():
    d = VideoDownloader()
    assert d._classify_failure("ERROR: This video requires login", "https://x/y").code == (
        "LOGIN_REQUIRED_NOT_SUPPORTED"
    )
    assert d._classify_failure("ERROR: DRM protected stream", "https://x/y").code == (
        "DRM_NOT_SUPPORTED"
    )
    assert d._classify_failure("ERROR: HTTP Error 429 Too Many Requests", "https://x/y").code == (
        "PLATFORM_BLOCKED_OR_RATE_LIMITED"
    )
