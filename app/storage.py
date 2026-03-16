from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from .config import ensure_directories, settings
from .models import DocumentRecord, utc_now_iso


class DocumentStore:
    def __init__(self) -> None:
        ensure_directories()
        self._lock = threading.RLock()
        self._documents: dict[str, DocumentRecord] = {}
        self._load()

    def _load(self) -> None:
        for json_path in settings.state_dir.glob("*.json"):
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            document = DocumentRecord.model_validate(payload)
            self._documents[document.id] = document

    def _state_path(self, document_id: str) -> Path:
        return settings.state_dir / f"{document_id}.json"

    def save(self, document: DocumentRecord) -> DocumentRecord:
        with self._lock:
            document.updated_at = utc_now_iso()
            self._documents[document.id] = document
            self._state_path(document.id).write_text(
                json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return document.model_copy(deep=True)

    def get(self, document_id: str) -> DocumentRecord:
        with self._lock:
            document = self._documents.get(document_id)
            if document is None:
                raise KeyError(document_id)
            return document.model_copy(deep=True)

    def mutate(self, document_id: str, mutator) -> DocumentRecord:
        with self._lock:
            document = self._documents.get(document_id)
            if document is None:
                raise KeyError(document_id)
            mutator(document)
            document.updated_at = utc_now_iso()
            self._documents[document.id] = document
            self._state_path(document.id).write_text(
                json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return document.model_copy(deep=True)

    def list(self) -> list[DocumentRecord]:
        with self._lock:
            documents = list(self._documents.values())
            documents.sort(key=lambda item: item.created_at, reverse=True)
            return [document.model_copy(deep=True) for document in documents]

    def delete(self, document_id: str) -> None:
        with self._lock:
            document = self._documents.pop(document_id, None)
            if document is None:
                raise KeyError(document_id)

            state_path = self._state_path(document_id)
            if state_path.exists():
                state_path.unlink()

            upload_dir = settings.uploads_dir / document_id
            asset_dir = settings.assets_dir / document_id
            shutil.rmtree(upload_dir, ignore_errors=True)
            shutil.rmtree(asset_dir, ignore_errors=True)

    def asset_dir(self, document_id: str) -> Path:
        path = settings.assets_dir / document_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def upload_path(self, document_id: str, filename: str) -> Path:
        upload_dir = settings.uploads_dir / document_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir / filename


store = DocumentStore()
