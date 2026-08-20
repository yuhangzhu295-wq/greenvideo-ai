from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import delete_history, list_history
from app.schemas import HistoryItemDTO, HistoryResponse

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history", response_model=HistoryResponse)
def get_history(
    type: str = Query(
        default="all",
        pattern="^(all|download|watermark|processing|success|failed)$",
    ),
    status: str | None = Query(default=None),
) -> HistoryResponse:
    return HistoryResponse(
        items=[
            HistoryItemDTO(
                id=row.id,
                task_id=row.task_id,
                title=row.title,
                kind=row.kind,
                thumbnail=row.thumbnail,
                status=row.status,
                size_mb=row.size_mb,
                created_at=row.created_at,
            )
            for row in list_history(kind_filter=type, status_filter=status)
        ]
    )


@router.delete("/history/{history_id}")
def delete_history_item(history_id: str) -> dict:
    if not delete_history(history_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "历史记录不存在。"},
        )
    return {"ok": True}
