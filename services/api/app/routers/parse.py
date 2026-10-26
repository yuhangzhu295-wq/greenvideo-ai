from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException

from app.db import insert_parse_result
from app.schemas import ParseRequest, ParseResultDTO
from app.services.downloader import (
    DownloadError,
    VideoDownloader,
    classify_platform,
    extract_url_from_share_text,
)
from app.services.security import SecurityError

router = APIRouter(prefix="/api", tags=["parse"])
downloader = VideoDownloader()


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post("/parse", response_model=ParseResultDTO)
def parse_video(payload: ParseRequest) -> ParseResultDTO:
    try:
        clean_url = extract_url_from_share_text(payload.input_text)
        result = downloader.parse_url(clean_url)
    except SecurityError as exc:
        raise _business_error(400, "UNSAFE_URL", str(exc)) from exc
    except DownloadError as exc:
        raise _business_error(422, exc.code, exc.message) from exc

    platform = classify_platform(clean_url)
    insert_parse_result(
        {
            "id": result.id,
            "url_hash": hashlib.sha256(clean_url.encode("utf-8")).hexdigest(),
            "url": clean_url,
            "title": result.title,
            "source": result.source or platform,
            "thumbnail": result.thumbnail,
            "duration": result.duration,
            "published_at": result.published_at,
            "formats": [item.model_dump() for item in result.formats],
        }
    )
    return result
