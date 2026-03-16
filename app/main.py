from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ensure_directories, settings
from .models import Block, DocumentRecord, TranslationRequest, UploadResponse
from .parsers import parse_docx, parse_pdf, parse_txt
from .services import DeepSeekClient, PreviewUnavailableError, TranslatorService, ensure_document_preview
from .storage import store


ensure_directories()

app = FastAPI(title="Local Document Translation Studio")
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
app.mount("/assets", StaticFiles(directory=settings.assets_dir), name="assets")

translator = TranslatorService(store=store, client=DeepSeekClient())


def detect_kind(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".txt":
        return "txt"
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")


def parse_document(kind: str, source_path: Path, document_id: str) -> list[Block]:
    asset_dir = store.asset_dir(document_id)
    if kind == "txt":
        return parse_txt(source_path)
    if kind == "docx":
        return parse_docx(source_path, asset_dir, document_id)
    if kind == "pdf":
        return parse_pdf(source_path, asset_dir, document_id)
    raise HTTPException(status_code=400, detail=f"Unsupported document kind: {kind}")


def require_document(document_id: str) -> DocumentRecord:
    try:
        return store.get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


def document_has_translation(document: DocumentRecord) -> bool:
    for block in document.blocks:
        if block.type != "page_break" and block.translated_text:
            return True
        if block.translated_rows:
            return True
        if block.translated_caption:
            return True
    return False


def build_translation_txt(document: DocumentRecord) -> str:
    parts: list[str] = []
    for block in document.blocks:
        if block.type in {"heading", "paragraph", "page_break"}:
            if block.translated_text:
                parts.append(block.translated_text)
        elif block.type == "table":
            if block.translated_rows:
                parts.append("[Table]")
                parts.extend("\t".join(cell or "" for cell in row) for row in block.translated_rows)
        elif block.type == "image":
            caption = block.translated_caption or ""
            if caption:
                label = "[Table]" if caption.lower().startswith("table") else "[Image]"
                parts.append(f"{label} {caption}")
    return "\n\n".join(part for part in parts if part.strip()) + "\n"


def translation_download_name(document: DocumentRecord) -> str:
    stem = Path(document.name).stem or "document"
    return f"{stem}.translated.txt"


def preview_url_for(document: DocumentRecord) -> str | None:
    if document.kind not in {"pdf", "docx"}:
        return None
    return f"/api/documents/{document.id}/preview"


def enrich_document(document: DocumentRecord) -> DocumentRecord:
    enriched = document.model_copy(deep=True)
    enriched.preview_url = preview_url_for(enriched)
    return enriched


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/api/documents", response_model=list[DocumentRecord])
def list_documents() -> list[DocumentRecord]:
    return [enrich_document(document) for document in store.list()]


@app.get("/api/documents/{document_id}", response_model=DocumentRecord)
def get_document(document_id: str) -> DocumentRecord:
    return enrich_document(require_document(document_id))


@app.post("/api/documents", response_model=UploadResponse)
async def upload_documents(files: list[UploadFile] = File(...)) -> UploadResponse:
    documents: list[DocumentRecord] = []
    for upload in files:
        document_id = uuid4().hex
        kind = detect_kind(upload.filename)
        source_path = store.upload_path(document_id, upload.filename)
        content = await upload.read()
        source_path.write_bytes(content)
        blocks = parse_document(kind, source_path, document_id)
        document = DocumentRecord(
            id=document_id,
            name=upload.filename,
            kind=kind,
            source_path=str(source_path),
            blocks=blocks,
        )
        documents.append(enrich_document(store.save(document)))
    return UploadResponse(documents=documents)


@app.post("/api/documents/{document_id}/translate", response_model=DocumentRecord)
def translate_document(document_id: str, request: TranslationRequest) -> DocumentRecord:
    require_document(document_id)
    translator.start_translation(document_id, request.target_language)
    return enrich_document(store.get(document_id))


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str) -> Response:
    require_document(document_id)
    store.delete(document_id)
    return Response(status_code=204)


@app.get("/api/documents/{document_id}/translation.txt")
def download_translation_txt(document_id: str) -> Response:
    document = require_document(document_id)
    if not document_has_translation(document):
        raise HTTPException(status_code=409, detail="Document has no translated content yet.")

    body = build_translation_txt(document)
    filename = translation_download_name(document)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.get("/api/documents/{document_id}/preview")
def preview_document(document_id: str) -> FileResponse:
    document = require_document(document_id)
    try:
        preview_path, media_type = ensure_document_preview(document)
    except PreviewUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return FileResponse(
        preview_path,
        media_type=media_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(preview_path.name)}",
        },
    )
