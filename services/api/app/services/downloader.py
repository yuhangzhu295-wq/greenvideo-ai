from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.schemas import FormatDTO, ParseResultDTO
from app.services.security import validate_public_http_url
from app.services.storage import new_id


class DownloadError(RuntimeError):
    """Business-safe error surfaced to API clients."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


URL_PATTERN = re.compile(r"https?://[^\s<>'\"，。！？、；；]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;!?，。！？；：:)）】]}>\"'"

PROTECTED_ERROR_MARKERS = (
    "login",
    "log in",
    "private",
    "cookies",
    "cookie",
    "captcha",
    "验证码",
    "登录",
)
DRM_ERROR_MARKERS = ("drm", "widevine", "copyright", "版权")
NETWORK_ERROR_MARKERS = (
    "ssl",
    "eof occurred",
    "connection reset",
    "connection aborted",
    "connection refused",
    "timed out",
    "timeout",
    "temporary failure in name resolution",
    "getaddrinfo",
    "network is unreachable",
    "remote end closed connection",
)
RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "blocked",
    "forbidden",
    "unavailable",
    "unsupported url",
    "not supported",
    "http error 403",
    "http error 429",
    "http error 412",
    "反爬",
    "限制",
)


def extract_url_from_share_text(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    if not match:
        raise DownloadError("NO_SHARE_LINK_FOUND", "未在分享文案中找到 http/https 视频链接。")
    return match.group(0).rstrip(TRAILING_PUNCTUATION)


def classify_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "douyin" in host or "iesdouyin" in host:
        return "douyin"
    if "bilibili" in host or host == "b23.tv":
        return "bilibili"
    if "kuaishou" in host or "gifshow" in host:
        return "kuaishou"
    if "youtube" in host or host == "youtu.be":
        return "youtube"
    return "unknown"


def _format_upload_date(value: object | None) -> str:
    if not value:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    value = str(value)
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]} 00:00"
    return value


@dataclass
class VideoDownloader:
    timeout_seconds: int = 45

    def parse_url(self, url: str) -> ParseResultDTO:
        if url.startswith("demo://") or url.rstrip("/") == "https://example.com/demo":
            return self._demo_result(url)

        public_url = validate_public_http_url(url)
        yt_dlp_path = self._find_ytdlp()
        if not yt_dlp_path:
            raise DownloadError(
                "PLATFORM_BLOCKED_OR_RATE_LIMITED",
                "当前运行环境未找到 yt-dlp，真实平台链接无法解析；"
                "可先使用演示链接或上传本地授权素材继续体验。",
            )
        return self._parse_with_ytdlp(public_url, yt_dlp_path)

    def create_download_task(self, parse_id: str, format_id: str) -> str:
        if not parse_id or not format_id:
            raise DownloadError("PARSE_FAILED", "缺少解析结果或下载格式。")
        return new_id("task")

    def _find_ytdlp(self) -> str | None:
        """Prefer the interpreter-local yt-dlp over whatever is on PATH.

        An older global install on PATH shadowed the pinned version and hit a
        Cloudflare anti-bot challenge, which surfaced as a generic "parse
        failed" even though the project's own dependency worked.
        """
        exe_dir = Path(sys.executable).parent
        for candidate in ("yt-dlp.exe", "yt-dlp"):
            local = exe_dir / candidate
            if local.exists():
                return str(local)
        return shutil.which("yt-dlp")

    def _classify_failure(self, text: str, url: str) -> DownloadError:
        lower = text.lower()
        if any(marker in lower for marker in DRM_ERROR_MARKERS):
            return DownloadError("DRM_NOT_SUPPORTED", "不支持 DRM、版权保护或付费内容绕过。")
        if any(marker in lower for marker in PROTECTED_ERROR_MARKERS):
            return DownloadError(
                "LOGIN_REQUIRED_NOT_SUPPORTED",
                "该视频可能需要登录、Cookie、验证码或私密权限，本项目不支持此类绕过。",
            )
        if "private" in lower:
            return DownloadError(
                "PRIVATE_VIDEO_NOT_SUPPORTED",
                "该视频为私密或不可公开视频，无法解析。",
            )
        # Checked before the platform-block markers: a TLS/connection failure is
        # not a platform restriction, and telling the user to "try later" is
        # more useful than implying their link is unsupported.
        if any(marker in lower for marker in NETWORK_ERROR_MARKERS):
            return DownloadError(
                "NETWORK_ERROR",
                "访问视频源时网络连接中断或被重置，请重试；若持续失败，可上传本地授权素材处理。",
            )
        if any(marker in lower for marker in RATE_LIMIT_MARKERS):
            return DownloadError(
                "PLATFORM_BLOCKED_OR_RATE_LIMITED",
                "平台限制、反爬或频率限制导致解析失败，可上传本地授权素材继续处理。",
            )
        if classify_platform(url) != "unknown":
            return DownloadError(
                "PLATFORM_BLOCKED_OR_RATE_LIMITED",
                "真实平台链接解析失败，可能受平台限制、短链风控或公开视频访问限制影响。",
            )
        return DownloadError("PARSE_FAILED", "公开视频解析失败，请检查链接是否可公开访问。")

    def _parse_with_ytdlp(self, url: str, executable_path: str) -> ParseResultDTO:
        result = subprocess.run(
            [
                executable_path,
                "--dump-json",
                "--no-playlist",
                "--skip-download",
                "--no-warnings",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            raise self._classify_failure(f"{result.stderr}\n{result.stdout}", url)

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError("PARSE_FAILED", "yt-dlp 返回的数据不是有效 JSON。") from exc

        formats = self._select_formats(data.get("formats", []))
        return ParseResultDTO(
            id=new_id("parse"),
            title=data.get("title") or "解析完成",
            source=data.get("extractor") or classify_platform(url) or "公开视频",
            thumbnail=data.get("thumbnail") or "/static/demo/thumb.svg",
            duration=int(data.get("duration") or 0),
            published_at=_format_upload_date(data.get("upload_date") or data.get("timestamp")),
            formats=formats,
        )

    def _select_formats(self, formats: list[dict]) -> list[FormatDTO]:
        """Describe the formats that actually exist.

        Missing metadata is reported as unknown (size 0.0, generic label) rather
        than filled in with a plausible-looking number: a fabricated size would
        tell the user something about the file that nobody measured.
        """
        mp4_formats = [
            fmt
            for fmt in formats
            if fmt.get("ext") == "mp4" and fmt.get("vcodec") not in {None, "none"}
        ]
        audio_formats = [
            fmt
            for fmt in formats
            if fmt.get("acodec") not in {None, "none"} and fmt.get("vcodec") in {None, "none"}
        ]

        def size_mb(item: dict) -> float:
            byte_size = item.get("filesize") or item.get("filesize_approx")
            return round(byte_size / 1024 / 1024, 2) if byte_size else 0.0

        selected: list[FormatDTO] = []
        if mp4_formats:
            heights = sorted(
                {(fmt.get("height") or 0) for fmt in mp4_formats}, reverse=True
            )
            for height in heights[:2]:
                item = next(
                    fmt for fmt in mp4_formats if (fmt.get("height") or 0) == height
                )
                selected.append(
                    FormatDTO(
                        format_id=str(item.get("format_id") or f"{height}p"),
                        label=f"视频 {height}P" if height else "视频 原始画质",
                        ext="mp4",
                        size_mb=size_mb(item),
                    )
                )
        else:
            # A direct media URL exposes no per-format metadata, so offer one
            # honest option instead of inventing two resolutions.
            selected.append(
                FormatDTO(format_id="best", label="原始文件", ext="mp4", size_mb=0.0)
            )

        if audio_formats:
            audio = audio_formats[0]
            selected.append(
                FormatDTO(
                    format_id=str(audio.get("format_id") or "mp3"),
                    label="音频文件 MP3",
                    ext="mp3",
                    size_mb=size_mb(audio),
                )
            )
        else:
            selected.append(
                FormatDTO(format_id="mp3", label="音频文件 MP3", ext="mp3", size_mb=0.0)
            )
        return selected

    def _demo_result(self, url: str) -> ParseResultDTO:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        return ParseResultDTO(
            id=f"parse_{digest}",
            title="解析完成",
            source="演示视频",
            thumbnail="/static/demo/thumb.svg",
            duration=225,
            published_at="2024-05-20 14:23",
            formats=[
                FormatDTO(format_id="1080p", label="高清视频 1080P", ext="mp4", size_mb=52.36),
                FormatDTO(format_id="720p", label="高清视频 720P", ext="mp4", size_mb=23.14),
                FormatDTO(format_id="mp3", label="音频文件 MP3", ext="mp3", size_mb=4.85),
            ],
        )

    def build_selector(self, format_id: str) -> tuple[str, bool]:
        """Map a client format id to a yt-dlp format selector.

        Returns ``(selector, audio_only)``. A bare video format id from the
        parse result is usually video-only, so pairing it with the best audio
        track is what makes the downloaded file playable with sound.
        """
        fid = (format_id or "").strip()
        if fid == "1080p":
            return (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                "/best[height<=1080][ext=mp4]/best[height<=1080]/best",
                False,
            )
        if fid == "720p":
            return (
                "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
                "/best[height<=720][ext=mp4]/best[height<=720]/best",
                False,
            )
        if fid == "mp3":
            return ("bestaudio/best", True)
        if not fid:
            return ("best", False)
        return (f"{fid}+bestaudio/{fid}/best", False)

    def download(
        self,
        url: str,
        format_id: str,
        output_dir: Path,
        progress_cb: Callable[[int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Path:
        """Download one public video/audio and return the produced file.

        Every failure is raised as a DownloadError with a business-safe code;
        nothing is written to look like a finished download when it is not.
        """
        if not url:
            raise DownloadError(
                "PARSE_RESULT_EXPIRED",
                "解析结果没有记录来源链接（可能是旧版本解析的），请重新解析后再下载。",
            )
        public_url = validate_public_http_url(url)
        yt_dlp_path = self._find_ytdlp()
        if not yt_dlp_path:
            raise DownloadError(
                "PLATFORM_BLOCKED_OR_RATE_LIMITED",
                "当前运行环境未找到 yt-dlp，无法执行真实下载。",
            )

        selector, audio_only = self.build_selector(format_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        template = str(output_dir / "download.%(ext)s")

        cmd = [
            yt_dlp_path,
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--newline",
            "-f", selector,
            "-o", template,
        ]
        if audio_only:
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            cmd += ["--merge-output-format", "mp4"]
        cmd.append(public_url)

        if progress_cb:
            progress_cb(30, "开始下载")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds * 20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise DownloadError(
                "DOWNLOAD_TIMEOUT",
                f"下载超时（{self.timeout_seconds * 20} 秒），请稍后重试或改用本地上传。",
            ) from None

        if cancel_check is not None and cancel_check():
            for produced in output_dir.glob("download.*"):
                produced.unlink(missing_ok=True)
            raise DownloadError("TASK_CANCELED", "任务已被用户取消。")

        if process.returncode != 0:
            raise self._classify_failure(f"{stderr}\n{stdout}", public_url)

        produced = sorted(
            output_dir.glob("download.*"), key=lambda p: p.stat().st_size, reverse=True
        )
        produced = [p for p in produced if p.stat().st_size > 0]
        if not produced:
            raise DownloadError(
                "DOWNLOAD_FAILED",
                "yt-dlp 报告成功但没有生成任何文件，未提供下载结果。",
            )
        if progress_cb:
            progress_cb(85, "下载完成，校验文件")
        return produced[0]
