from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import settings


class SecurityError(ValueError):
    """Raised when user-provided input violates security boundaries."""


LOGIN_STATE_KEYS = {
    "cookie",
    "cookies",
    "session",
    "sessionid",
    "sid",
    "token",
    "access_token",
    "auth",
    "authorization",
    "passport",
    "login",
}


def _is_blocked_ip(hostname: str) -> bool:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError("仅支持 http/https 公开视频链接。")

    hostname = parsed.hostname
    if not hostname:
        raise SecurityError("视频链接缺少有效域名。")

    normalized_host = hostname.lower().strip("[]")
    if normalized_host in {"localhost", "localhost.localdomain"}:
        raise SecurityError("不允许解析本机地址。")
    if normalized_host.endswith(".local"):
        raise SecurityError("不允许解析本地域名。")
    if _is_blocked_ip(normalized_host):
        raise SecurityError("不允许解析内网、回环或保留地址。")

    query_keys = {key.lower() for key in parse_qs(parsed.query).keys()}
    if query_keys & LOGIN_STATE_KEYS:
        raise SecurityError("链接中包含登录态或敏感参数，无法处理。")

    return parsed.geturl()


def safe_join_storage(filename: str, sub_dir: str = "files") -> Path:
    base = (settings.storage_dir / sub_dir).resolve()
    target = (base / filename).resolve()
    try:
        common = os.path.commonpath([str(base), str(target)])
    except ValueError as exc:
        raise SecurityError("非法文件路径：存在路径穿越风险。") from exc
    if common != str(base):
        raise SecurityError("非法文件路径：存在路径穿越风险。")
    return target
