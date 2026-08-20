from __future__ import annotations

from fastapi import APIRouter

from app.db import insert_feedback
from app.schemas import FeedbackRequest, FeedbackResponse
from app.services.storage import new_id

router = APIRouter(prefix="/api", tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackRequest) -> FeedbackResponse:
    feedback_id = new_id("fb")
    insert_feedback({"id": feedback_id, **payload.model_dump()})
    return FeedbackResponse(ok=True, id=feedback_id)
