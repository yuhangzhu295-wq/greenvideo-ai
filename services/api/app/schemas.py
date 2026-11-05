from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FormatDTO(BaseModel):
    format_id: str
    label: str
    ext: str
    size_mb: float


class ParseRequest(BaseModel):
    url: str | None = Field(default=None, max_length=4000)
    text: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_url_or_text(self) -> ParseRequest:
        if not (self.url or self.text):
            raise ValueError("请提供视频链接或完整分享文案。")
        return self

    @property
    def input_text(self) -> str:
        return (self.text or self.url or "").strip()


class ParseResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source: str
    thumbnail: str
    duration: int
    published_at: str
    formats: list[FormatDTO]


class DownloadTaskRequest(BaseModel):
    parse_id: str = Field(min_length=1, max_length=120)
    format_id: str = Field(min_length=1, max_length=120)


class MaskRegion(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class TimedMaskRegion(BaseModel):
    """A mask region that only applies to part of the video.

    Watermarks often change layout mid-clip (a corner overlay during the content,
    a centred end card at the tail). Without a time scope a single static mask
    either misses the second layout or masks frames where nothing is there.
    """

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    start_s: float = Field(default=0.0, ge=0)
    end_s: float | None = Field(default=None, gt=0)


MaskPreset = Literal[
    "douyin_top_left",
    "douyin_bottom_right",
    "douyin_both_corners",
    "custom",
]
RepairMode = Literal["opencv", "ai"]


class WatermarkTaskRequest(BaseModel):
    source_task_id: str | None = Field(default=None, max_length=120)
    parse_id: str | None = Field(default=None, max_length=120)
    video_url: str | None = Field(default=None, max_length=4000)
    mask_regions: list[MaskRegion] = Field(default_factory=list)
    timed_mask_regions: list[TimedMaskRegion] = Field(default_factory=list)
    mask_preset: MaskPreset = "douyin_both_corners"
    repair_mode: RepairMode = "opencv"
    remove_subtitle: bool = False
    confirm_authorized: bool = False
    mode: Literal["standard", "ai"] | None = None
    auto_detect: bool = False

    @model_validator(mode="after")
    def normalize_legacy_mode(self) -> WatermarkTaskRequest:
        if self.mode == "ai":
            self.repair_mode = "ai"
        elif self.mode == "standard":
            self.repair_mode = "opencv"
        return self


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str


class TaskStatusDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    type: str
    status: str
    progress: int
    stage: str
    eta_seconds: int
    download_url: str | None = None
    media_type: Literal["video", "image", "audio"] | None = None
    tracking_preview_url: str | None = None
    source_video_url: str | None = None
    frame_tracks: list[dict] | None = None
    video_width: int | None = None
    video_height: int | None = None
    unconfirmed_candidates: list[dict] | None = None
    requires_confirmation: bool = False
    error: str | None = None


class HistoryItemDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str | None = None
    title: str
    kind: str
    thumbnail: str
    status: str
    size_mb: float
    created_at: str


class HistoryResponse(BaseModel):
    items: list[HistoryItemDTO]


class FeedbackRequest(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    contact: str = Field(default="", max_length=120)


class FeedbackResponse(BaseModel):
    ok: bool
    id: str
