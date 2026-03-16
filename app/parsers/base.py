from __future__ import annotations


def normalize_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def image_url(document_id: str, file_name: str) -> str:
    return f"/assets/{document_id}/{file_name}"

