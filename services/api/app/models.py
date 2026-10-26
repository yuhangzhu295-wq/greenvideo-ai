from __future__ import annotations

from typing import Any

from sqlmodel import Field, SQLModel


class SubscriptableModel(SQLModel):
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def keys(self) -> list[str]:
        return list(self.model_fields.keys())


class User(SubscriptableModel, table=True):
    __tablename__ = "users"
    id: str = Field(primary_key=True)
    nickname: str
    member_level: str
    daily_fast_quota: int


class ParseResult(SubscriptableModel, table=True):
    __tablename__ = "parse_results"
    id: str = Field(primary_key=True)
    url_hash: str
    # The source URL is needed by the download task. Storing only its hash made
    # a real download impossible, which is why the download step had no honest
    # implementation.
    url: str | None = Field(default=None, nullable=True)
    title: str
    source: str
    thumbnail: str
    duration: int
    published_at: str
    formats_json: str
    created_at: str


class Task(SubscriptableModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(primary_key=True)
    type: str
    status: str
    progress: int
    stage: str
    input_json: str
    output_path: str | None = Field(default=None, nullable=True)
    error: str | None = Field(default=None, nullable=True)
    created_at: str
    updated_at: str


class HistoryItem(SubscriptableModel, table=True):
    __tablename__ = "history_items"
    id: str = Field(primary_key=True)
    task_id: str | None = Field(default=None, nullable=True)
    title: str
    kind: str
    thumbnail: str
    status: str
    size_mb: float
    created_at: str


class Feedback(SubscriptableModel, table=True):
    __tablename__ = "feedback"
    id: str = Field(primary_key=True)
    category: str
    description: str
    contact: str
    created_at: str
