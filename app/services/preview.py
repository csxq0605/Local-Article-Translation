from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import DocumentRecord


PREVIEW_FILE_NAME = "_original-preview.pdf"


class PreviewUnavailableError(RuntimeError):
    pass


def preview_file_path(document: DocumentRecord) -> Path:
    source_path = Path(document.source_path)
    return source_path.parent / PREVIEW_FILE_NAME


def ensure_document_preview(document: DocumentRecord) -> tuple[Path, str]:
    source_path = Path(document.source_path)
    if not source_path.exists():
        raise PreviewUnavailableError("Source file is missing.")

    if document.kind == "pdf":
        return source_path, "application/pdf"

    if document.kind != "docx":
        raise PreviewUnavailableError("Original preview is only available for PDF and DOCX documents.")

    output_path = preview_file_path(document)
    if output_path.exists() and output_path.stat().st_mtime >= source_path.stat().st_mtime:
        return output_path, "application/pdf"

    export_docx_to_pdf(source_path, output_path)
    return output_path, "application/pdf"


def export_docx_to_pdf(source_path: Path, output_path: Path) -> None:
    source = str(source_path.resolve()).replace("'", "''")
    target = str(output_path.resolve()).replace("'", "''")
    script = f"""
$src = '{source}'
$dst = '{target}'
$word = $null
$doc = $null
try {{
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $doc = $word.Documents.Open($src, $false, $true)
  $doc.ExportAsFixedFormat($dst, 17)
}} finally {{
  if ($doc -ne $null) {{
    $doc.Close($false) | Out-Null
  }}
  if ($word -ne $null) {{
    $word.Quit() | Out-Null
  }}
  [gc]::Collect()
  [gc]::WaitForPendingFinalizers()
}}
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreviewUnavailableError("Word preview generation timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise PreviewUnavailableError(f"Word preview generation failed: {detail}") from exc
        raise PreviewUnavailableError("Word preview generation failed.") from exc
