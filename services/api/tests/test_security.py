from __future__ import annotations

import pytest

from app.services.downloader import extract_url_from_share_text
from app.services.security import SecurityError, validate_public_http_url


def test_extracts_urls_from_share_texts() -> None:
    assert (
        extract_url_from_share_text(
            "4.17 复制打开抖音，看看【田宇轩的作品】事实证明是58还是能有爱情的吗？ "
            "https://v.douyin.com/qu2PVcn74Lw/ E@H.vf"
        )
        == "https://v.douyin.com/qu2PVcn74Lw/"
    )
    assert (
        extract_url_from_share_text("【我给Codex做了一个分镜台】https://b23.tv/UlH6LvD")
        == "https://b23.tv/UlH6LvD"
    )
    assert (
        extract_url_from_share_text("https://v.kuaishou.com/KDINf2L9 用最平静的声音说出最绝望的话")
        == "https://v.kuaishou.com/KDINf2L9"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a.mp4",
        "ftp://example.com/a.mp4",
        "http://localhost/video",
        "http://127.0.0.1/video",
        "http://10.0.0.1/video",
        "http://172.16.1.2/video",
        "http://192.168.1.2/video",
        "http://169.254.1.2/video",
        "http://example.com/video?cookie=abc",
    ],
)
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(SecurityError):
        validate_public_http_url(url)


def test_allows_public_http_url() -> None:
    assert validate_public_http_url("https://example.com/demo") == "https://example.com/demo"
