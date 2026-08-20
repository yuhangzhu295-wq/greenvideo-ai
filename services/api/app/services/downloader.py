from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
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
        exe_dir = str(Path(sys.executable).parent)
        return shutil.which("yt-dlp") or shutil.which("yt-dlp", path=exe_dir)

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

        high = max(mp4_formats, key=lambda item: item.get("height") or 0, default={})
        low = min(mp4_formats, key=lambda item: item.get("height") or 9999, default={})
        audio = audio_formats[0] if audio_formats else {}

        def size_mb(item: dict, fallback: float) -> float:
            byte_size = item.get("filesize") or item.get("filesize_approx")
            return round(byte_size / 1024 / 1024, 2) if byte_size else fallback

        return [
            FormatDTO(
                format_id=str(high.get("format_id") or "1080p"),
                label="高清视频 1080P",
                ext="mp4",
                size_mb=size_mb(high, 52.36),
            ),
            FormatDTO(
                format_id=str(low.get("format_id") or "720p"),
                label="高清视频 720P",
                ext="mp4",
                size_mb=size_mb(low, 23.14),
            ),
            FormatDTO(
                format_id=str(audio.get("format_id") or "mp3"),
                label="音频文件 MP3",
                ext="mp3",
                size_mb=size_mb(audio, 4.85),
            ),
        ]

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
