from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


BlockType = Literal["heading", "paragraph", "table", "image", "page_break"]
DocumentStatus = Literal["ready", "translating", "completed", "failed"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Block(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: BlockType
    order: int
    page: int | None = None
    level: int | None = None
    text: str | None = None
    translated_text: str | None = None
    rows: list[list[str]] | None = None
    translated_rows: list[list[str]] | None = None
    image_url: str | None = None
    caption: str | None = None
    translated_caption: str | None = None
    caption_position: Literal["above", "below", "left", "right"] | None = None


class TranslationSession(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    target_language: str
    model: str
    created_at: str = Field(default_factory=utc_now_iso)


class DocumentRecord(BaseModel):
    id: str
    name: str
    kind: str
    source_path: str
    preview_url: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    status: DocumentStatus = "ready"
    progress: float = 0.0
    error: str | None = None
    target_language: str | None = None
    session: TranslationSession | None = None
    blocks: list[Block] = Field(default_factory=list)


class TranslationRequest(BaseModel):
    target_language: str = "Chinese (Simplified)"


class UploadResponse(BaseModel):
    documents: list[DocumentRecord]
