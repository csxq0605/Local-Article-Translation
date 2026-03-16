from __future__ import annotations

import re
import threading
from uuid import uuid4

from ..config import settings
from ..models import Block, DocumentRecord, TranslationSession
from ..storage import DocumentStore
from .deepseek_client import DeepSeekClient

REFERENCE_HEADING_RE = re.compile(
    r"^(?:(?:[IVXLC]+|[A-Z]|\d+)[.)]?\s*)?"
    r"(references?|bibliography|参考文献|参考资料|参考书目)$",
    re.IGNORECASE,
)


def split_text_for_translation(text: str, limit: int) -> list[str]:
    stripped = text.strip()
    if len(stripped) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    segments = re.split(r"(\n+|(?<=[.!?\u3002\uFF01\uFF1F])\s+)", text)
    for segment in segments:
        if not segment:
            continue
        if len(current) + len(segment) <= limit:
            current += segment
            continue
        if current:
            parts.append(current)
            current = ""
        if len(segment) <= limit:
            current = segment
            continue
        for start in range(0, len(segment), limit):
            parts.append(segment[start : start + limit])
    if current:
        parts.append(current)
    return parts


def normalize_heading_text(text: str) -> str:
    compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
    compact = re.sub(r"\s+", " ", compact).strip()
    compact = compact.strip(" .:：;；,，")
    return compact


def is_reference_heading(block: Block) -> bool:
    if block.type not in {"heading", "paragraph"}:
        return False
    text = normalize_heading_text(block.text or "")
    if not text or len(text) > 80:
        return False
    return bool(REFERENCE_HEADING_RE.match(text))


def find_reference_block_ids(blocks: list[Block]) -> set[str]:
    reference_start: int | None = None
    for index, block in enumerate(blocks):
        if is_reference_heading(block):
            reference_start = index
            break

    if reference_start is None:
        return set()

    return {block.id for block in blocks[reference_start:]}


class TranslatorService:
    def __init__(self, store: DocumentStore, client: DeepSeekClient) -> None:
        self.store = store
        self.client = client
        self._active_jobs: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_translation(self, document_id: str, target_language: str) -> None:
        with self._lock:
            current = self._active_jobs.get(document_id)
            if current and current.is_alive():
                return

            self.store.mutate(document_id, lambda document: self._prepare_document(document, target_language))

            thread = threading.Thread(
                target=self._translate_document,
                args=(document_id,),
                daemon=True,
                name=f"translate-{document_id}",
            )
            self._active_jobs[document_id] = thread
            thread.start()

    def _prepare_document(self, document: DocumentRecord, target_language: str) -> None:
        reference_block_ids = find_reference_block_ids(document.blocks)
        document.status = "translating"
        document.progress = 0.0
        document.error = None
        document.target_language = target_language
        document.session = TranslationSession(
            id=uuid4().hex,
            target_language=target_language,
            model=settings.deepseek_model,
        )
        for block in document.blocks:
            keep_source = block.id in reference_block_ids
            block.translated_text = (
                block.text
                if block.type == "page_break" or (keep_source and block.type in {"heading", "paragraph"})
                else None
            )
            block.translated_rows = block.rows if keep_source and block.type == "table" else None
            block.translated_caption = block.caption if keep_source and block.type == "image" else None

    def _translate_document(self, document_id: str) -> None:
        try:
            document = self.store.get(document_id)
            session = document.session
            if session is None:
                raise RuntimeError("Translation session was not initialized.")
            reference_block_ids = find_reference_block_ids(document.blocks)

            total = max(1, len(document.blocks))
            for index, block in enumerate(document.blocks, start=1):
                self._translate_block(
                    document,
                    block,
                    session,
                    skip_translation=block.id in reference_block_ids,
                )
                progress = index / total
                try:
                    self.store.mutate(document_id, lambda stored: self._update_block(stored, block, progress))
                except KeyError:
                    return

            try:
                self.store.mutate(document_id, self._finish_success)
            except KeyError:
                return
        except KeyError:
            return
        except Exception as exc:
            try:
                self.store.mutate(document_id, lambda stored: self._finish_failure(stored, str(exc)))
            except KeyError:
                return
        finally:
            with self._lock:
                self._active_jobs.pop(document_id, None)

    def _update_block(self, stored: DocumentRecord, block: Block, progress: float) -> None:
        for idx, current in enumerate(stored.blocks):
            if current.id == block.id:
                stored.blocks[idx] = block
                break
        stored.progress = progress

    def _finish_success(self, stored: DocumentRecord) -> None:
        stored.status = "completed"
        stored.progress = 1.0
        stored.error = None

    def _finish_failure(self, stored: DocumentRecord, error: str) -> None:
        stored.status = "failed"
        stored.error = error

    def _translate_block(
        self,
        document: DocumentRecord,
        block: Block,
        session: TranslationSession,
        *,
        skip_translation: bool = False,
    ) -> None:
        if skip_translation:
            if block.type in {"paragraph", "heading", "page_break"}:
                block.translated_text = block.text
            elif block.type == "table":
                block.translated_rows = block.rows
            elif block.type == "image":
                block.translated_caption = block.caption
            return

        if block.type in {"paragraph", "heading"}:
            block.translated_text = self._translate_text_block(
                block.text or "",
                session=session,
                block_kind=block.type,
                document_name=document.name,
            )
            return

        if block.type == "table":
            translated_rows: list[list[str]] = []
            for row in block.rows or []:
                translated_row: list[str] = []
                for cell in row:
                    translated_row.append(
                        self._translate_text_block(
                            cell,
                            session=session,
                            block_kind="table cell",
                            document_name=document.name,
                        )
                    )
                translated_rows.append(translated_row)
            block.translated_rows = translated_rows
            return

        if block.type == "image":
            if block.caption:
                block.translated_caption = self._translate_text_block(
                    block.caption,
                    session=session,
                    block_kind="image caption",
                    document_name=document.name,
                )
            return

        if block.type == "page_break":
            block.translated_text = block.text

    def _translate_text_block(
        self,
        text: str,
        *,
        session: TranslationSession,
        block_kind: str,
        document_name: str,
    ) -> str:
        if not text.strip():
            return text

        parts = split_text_for_translation(text, settings.translation_chunk_chars)
        translated_parts = [
            self.client.translate_text(
                session=session,
                source_text=part,
                block_kind=block_kind,
                document_name=document_name,
            )
            for part in parts
        ]
        return "".join(translated_parts)
