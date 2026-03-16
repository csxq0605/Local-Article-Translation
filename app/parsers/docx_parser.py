from __future__ import annotations

import base64
import html
import mimetypes
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..models import Block
from .base import image_url, normalize_text


EMU_PER_PIXEL = 9525
CAPTION_RE = re.compile(r"^(fig(?:ure)?\.?|table)\s*\d+[a-z]?\s*[:.]", re.IGNORECASE)
SHAPE_SIZE_RE = re.compile(r"(width|height)\s*:\s*([0-9.]+)pt", re.IGNORECASE)
EQUATION_NUMBER_RE = re.compile(r"^\((?:S?\d+(?:\.\d+)?)\)$", re.IGNORECASE)


@dataclass(slots=True)
class ParagraphToken:
    paragraph: Paragraph
    text: str
    style_name: str
    media: list[dict]
    equation_parts: list[dict]


@dataclass(slots=True)
class TableToken:
    table: Table


def iter_block_items(document: DocxDocument) -> Iterator[Paragraph | Table]:
    parent = document.element.body
    for child in parent.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def paragraph_media(paragraph: Paragraph) -> list[dict]:
    media: list[dict] = []
    for drawing in paragraph._element.xpath(".//w:drawing"):
        blips = drawing.xpath(".//a:blip")
        if not blips:
            continue
        rid = blips[0].get(qn("r:embed"))
        if not rid:
            continue

        width_px = 320
        height_px = 220
        extents = drawing.xpath(".//wp:extent")
        if extents:
            try:
                cx = int(extents[0].get("cx", "0"))
                cy = int(extents[0].get("cy", "0"))
                if cx > 0:
                    width_px = max(80, round(cx / EMU_PER_PIXEL))
                if cy > 0:
                    height_px = max(60, round(cy / EMU_PER_PIXEL))
            except Exception:
                pass

        media.append(
            {
                "rid": rid,
                "width_px": width_px,
                "height_px": height_px,
            }
        )
    return media


def shape_dimensions_px(style_value: str | None) -> tuple[int, int]:
    width_px = 320
    height_px = 80
    if not style_value:
        return width_px, height_px

    matches = {
        name.lower(): float(value)
        for name, value in SHAPE_SIZE_RE.findall(style_value)
    }
    if "width" in matches:
        width_px = max(40, round(matches["width"] * 96 / 72))
    if "height" in matches:
        height_px = max(24, round(matches["height"] * 96 / 72))
    return width_px, height_px


def paragraph_equation_parts(paragraph: Paragraph) -> list[dict]:
    if "ProgID=\"Equation" not in paragraph._element.xml:
        return []

    parts: list[dict] = []
    for child in paragraph._element.iterchildren():
        if child.tag != qn("w:r"):
            continue

        ole_objects = child.xpath(".//*[local-name()='OLEObject']")
        if ole_objects:
            prog_id = (ole_objects[0].get("ProgID") or "").lower()
            if "equation" in prog_id:
                imagedata = child.xpath(".//*[local-name()='imagedata']")
                if imagedata:
                    rid = imagedata[0].get(qn("r:id"))
                    if rid:
                        shape = child.xpath(".//*[local-name()='shape']")
                        style_value = shape[0].get("style") if shape else None
                        width_px, height_px = shape_dimensions_px(style_value)
                        parts.append(
                            {
                                "type": "image",
                                "kind": "equation",
                                "rid": rid,
                                "width_px": width_px,
                                "height_px": height_px,
                            }
                        )
                continue

        text_nodes = child.xpath(".//*[local-name()='t']/text()")
        if not text_nodes:
            continue
        text = re.sub(r"\s+", " ", "".join(text_nodes)).strip()
        if not text:
            continue
        parts.append({"type": "text", "text": text})

    return parts


def extract_image_asset(
    paragraph: Paragraph,
    media_item: dict,
    asset_dir: Path,
    document_id: str,
    extracted_images: dict[str, str],
    image_index: int,
) -> tuple[str, int]:
    rid = media_item["rid"]
    if rid in extracted_images:
        return extracted_images[rid], image_index

    image_part = paragraph.part.related_parts[rid]
    suffix = Path(getattr(image_part, "partname", f"image-{image_index}.png")).suffix or ".png"
    file_name = f"docx-image-{image_index:04d}{suffix}"
    output_path = asset_dir / file_name
    output_path.write_bytes(image_part.blob)

    if output_path.suffix.lower() in {".emf", ".wmf"}:
        converted_path = output_path.with_suffix(".png")
        src = str(output_path.resolve()).replace("'", "''")
        dst = str(converted_path.resolve()).replace("'", "''")
        command = (
            "Add-Type -AssemblyName System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile('{src}'); "
            f"try {{ $img.Save('{dst}', [System.Drawing.Imaging.ImageFormat]::Png) }} "
            "finally { $img.Dispose() }"
        )
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    command,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            output_path = converted_path
        except Exception:
            output_path = output_path

    extracted_images[rid] = image_url(document_id, output_path.name)
    return extracted_images[rid], image_index + 1


def build_data_uri(asset_path: Path) -> str:
    mime_type = mimetypes.guess_type(asset_path.name)[0] or "image/png"
    payload = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def wrap_text(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        while len(line) > max_chars:
            lines.append(line[:max_chars])
            line = line[max_chars:]
        lines.append(line)
    return lines or [""]


def render_table_svg(table: Table, asset_dir: Path, image_index: int) -> str:
    rows_data: list[list[list[str]]] = []
    max_cols = 0
    for row in table.rows:
        cells: list[list[str]] = []
        for cell in row.cells:
            text = normalize_text(cell.text or "")
            cells.append([text] if text else [""])
        rows_data.append(cells)
        max_cols = max(max_cols, len(cells))

    if max_cols == 0:
        max_cols = 1

    normalized_rows: list[list[list[str]]] = []
    column_widths = [120] * max_cols
    char_px = 7.2
    padding_x = 12
    line_height = 18

    for row in rows_data:
        normalized_row: list[list[str]] = []
        for col_index in range(max_cols):
            cell_lines = row[col_index] if col_index < len(row) else [""]
            normalized_row.append(cell_lines)
            longest = max((len(line) for line in cell_lines), default=0)
            column_widths[col_index] = max(
                column_widths[col_index],
                min(360, max(120, int(longest * char_px + padding_x * 2))),
            )
        normalized_rows.append(normalized_row)

    wrapped_rows: list[list[list[str]]] = []
    row_heights: list[int] = []
    for row in normalized_rows:
        wrapped_row: list[list[str]] = []
        max_lines = 1
        for col_index, cell_lines in enumerate(row):
            width_chars = max(8, int((column_widths[col_index] - padding_x * 2) / char_px))
            wrapped_lines: list[str] = []
            for line in cell_lines:
                wrapped_lines.extend(wrap_text(line, width_chars))
            wrapped_lines = wrapped_lines or [""]
            wrapped_row.append(wrapped_lines)
            max_lines = max(max_lines, len(wrapped_lines))
        wrapped_rows.append(wrapped_row)
        row_heights.append(max(38, max_lines * line_height + 18))

    total_width = sum(column_widths)
    total_height = sum(row_heights)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{total_height}" viewBox="0 0 {total_width} {total_height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]

    y = 0
    for row_index, row in enumerate(wrapped_rows):
        x = 0
        row_height = row_heights[row_index]
        for col_index, cell_lines in enumerate(row):
            col_width = column_widths[col_index]
            fill = "#f3f0e8" if row_index == 0 else "#ffffff"
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{col_width}" height="{row_height}" fill="{fill}" stroke="#c9c1b3" stroke-width="1"/>'
            )
            text_y = y + 24
            for line in cell_lines:
                svg_parts.append(
                    f'<text x="{x + padding_x}" y="{text_y}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#22201d">{html.escape(line)}</text>'
                )
                text_y += line_height
            x += col_width
        y += row_height

    svg_parts.append("</svg>")
    file_name = f"docx-table-{image_index:04d}.svg"
    (asset_dir / file_name).write_text("".join(svg_parts), encoding="utf-8")
    return file_name


def render_composite_figure_svg(
    media_rows: list[list[dict]],
    asset_url_rows: list[list[str]],
    asset_dir: Path,
    image_index: int,
) -> str:
    if len(media_rows) != len(asset_url_rows):
        raise ValueError("Media row metadata and asset URL rows must have the same length.")

    column_gap = 16
    row_gap = 18
    row_widths: list[int] = []
    row_heights: list[int] = []

    for row in media_rows:
        if not row:
            continue
        row_width = sum(item["width_px"] for item in row) + column_gap * max(0, len(row) - 1)
        row_height = max(item["height_px"] for item in row)
        row_widths.append(row_width)
        row_heights.append(row_height)

    total_width = max(row_widths, default=320)
    total_height = sum(row_heights) + row_gap * max(0, len(row_heights) - 1)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{total_height}" viewBox="0 0 {total_width} {total_height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]

    y = 0
    for row_index, row in enumerate(media_rows):
        if not row:
            continue
        row_height = row_heights[row_index]
        row_width = row_widths[row_index]
        x = max(0, (total_width - row_width) / 2)
        for col_index, item in enumerate(row):
            asset_name = Path(asset_url_rows[row_index][col_index]).name
            asset_path = asset_dir / asset_name
            href = build_data_uri(asset_path)
            width_px = item["width_px"]
            height_px = item["height_px"]
            offset_x = x
            offset_y = y + max(0, (row_height - height_px) / 2)
            svg_parts.append(
                f'<image href="{html.escape(href)}" x="{offset_x}" y="{offset_y}" width="{width_px}" height="{height_px}" preserveAspectRatio="xMidYMid meet"/>'
            )
            x += width_px + column_gap
        y += row_height + row_gap

    svg_parts.append("</svg>")
    file_name = f"docx-figure-{image_index:04d}.svg"
    (asset_dir / file_name).write_text("".join(svg_parts), encoding="utf-8")
    return file_name


def render_equation_svg(
    parts: list[dict],
    asset_dir: Path,
    image_index: int,
) -> str:
    flow_parts = list(parts)
    equation_number: str | None = None

    if flow_parts and flow_parts[-1]["type"] == "text":
        candidate = flow_parts[-1]["text"]
        if EQUATION_NUMBER_RE.match(candidate):
            equation_number = candidate
            flow_parts = flow_parts[:-1]

    font_size = 24
    char_width = 11
    part_gap = 12
    left_padding = 18
    right_padding = 18
    number_slot = 92 if equation_number else 0

    content_width = 0
    content_height = 0
    measured_parts: list[dict] = []

    for part in flow_parts:
        measured = dict(part)
        if part["type"] == "image":
            measured["render_width"] = part["width_px"]
            measured["render_height"] = part["height_px"]
        else:
            measured["render_width"] = max(18, len(part["text"]) * char_width)
            measured["render_height"] = font_size + 8
        measured_parts.append(measured)
        content_width += measured["render_width"]
        content_height = max(content_height, measured["render_height"])

    if measured_parts:
        content_width += part_gap * (len(measured_parts) - 1)

    total_width = max(520, content_width + left_padding + right_padding + number_slot)
    total_height = max(48, content_height + 16)
    content_x = max(left_padding, (total_width - number_slot - content_width) / 2)
    center_y = total_height / 2

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{total_height}" viewBox="0 0 {total_width} {total_height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]

    cursor_x = content_x
    for part in measured_parts:
        if part["type"] == "image":
            asset_path = asset_dir / Path(part["asset_url"]).name
            href = build_data_uri(asset_path)
            y = center_y - part["render_height"] / 2
            svg_parts.append(
                f'<image href="{html.escape(href)}" x="{cursor_x}" y="{y}" width="{part["render_width"]}" height="{part["render_height"]}" preserveAspectRatio="xMidYMid meet"/>'
            )
        else:
            svg_parts.append(
                f'<text x="{cursor_x}" y="{center_y}" font-size="{font_size}" font-family="Times New Roman, serif" fill="#22201d" dominant-baseline="middle">{html.escape(part["text"])}</text>'
            )
        cursor_x += part["render_width"] + part_gap

    if equation_number:
        svg_parts.append(
            f'<text x="{total_width - right_padding}" y="{center_y}" font-size="{font_size}" font-family="Times New Roman, serif" fill="#22201d" text-anchor="end" dominant-baseline="middle">{html.escape(equation_number)}</text>'
        )

    svg_parts.append("</svg>")
    file_name = f"docx-equation-{image_index:04d}.svg"
    (asset_dir / file_name).write_text("".join(svg_parts), encoding="utf-8")
    return file_name


def is_caption_text(text: str, style_name: str) -> bool:
    compact = normalize_text(text)
    if not compact:
        return False
    if CAPTION_RE.match(compact):
        return True
    style_lower = style_name.lower()
    if "caption" in style_lower:
        return True
    if style_lower.startswith("heading 4") and CAPTION_RE.match(compact):
        return True
    return False


def paragraph_block_type(style_name: str) -> tuple[str, int | None]:
    style_lower = style_name.lower()
    if style_lower == "title":
        return "heading", 1
    if style_lower.startswith("heading"):
        digits = "".join(ch for ch in style_name if ch.isdigit())
        return "heading", int(digits) if digits else 1
    return "paragraph", None


def build_tokens(document: DocxDocument) -> list[ParagraphToken | TableToken]:
    tokens: list[ParagraphToken | TableToken] = []
    for item in iter_block_items(document):
        if isinstance(item, Paragraph):
            text = normalize_text(item.text or "")
            style_name = ""
            try:
                style_name = item.style.name or ""
            except Exception:
                style_name = ""
            tokens.append(
                ParagraphToken(
                    paragraph=item,
                    text=text,
                    style_name=style_name,
                    media=paragraph_media(item),
                    equation_parts=paragraph_equation_parts(item),
                )
            )
        else:
            tokens.append(TableToken(table=item))
    return tokens


def find_caption(
    tokens: list[ParagraphToken | TableToken],
    index: int,
    *,
    prefer_next: bool,
) -> tuple[str | None, int | None, str | None]:
    offsets = (1, -1) if prefer_next else (-1, 1)
    for offset in offsets:
        target = index + offset
        if target < 0 or target >= len(tokens):
            continue
        token = tokens[target]
        if not isinstance(token, ParagraphToken):
            continue
        if is_caption_text(token.text, token.style_name):
            return token.text, target, "below" if offset > 0 else "above"
    return None, None, None


def find_caption_for_range(
    tokens: list[ParagraphToken | TableToken],
    start_index: int,
    end_index: int,
    *,
    prefer_next: bool,
) -> tuple[str | None, int | None, str | None]:
    if prefer_next:
        next_index = end_index + 1
        if next_index < len(tokens):
            next_token = tokens[next_index]
            if isinstance(next_token, ParagraphToken) and is_caption_text(next_token.text, next_token.style_name):
                return next_token.text, next_index, "below"

        prev_index = start_index - 1
        if prev_index >= 0:
            prev_token = tokens[prev_index]
            if isinstance(prev_token, ParagraphToken) and is_caption_text(prev_token.text, prev_token.style_name):
                return prev_token.text, prev_index, "above"

        return None, None, None

    return find_caption(tokens, start_index, prefer_next=False)


def parse_docx(file_path: Path, asset_dir: Path, document_id: str) -> list[Block]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    document = Document(file_path)
    tokens = build_tokens(document)

    blocks: list[Block] = []
    order = 0
    image_index = 0
    extracted_images: dict[str, str] = {}
    consumed_caption_indexes: set[int] = set()

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if isinstance(token, ParagraphToken):
            if index in consumed_caption_indexes:
                index += 1
                continue

            if token.equation_parts:
                equation_parts: list[dict] = []
                for part in token.equation_parts:
                    if part["type"] == "image":
                        asset_url, image_index = extract_image_asset(
                            token.paragraph,
                            part,
                            asset_dir,
                            document_id,
                            extracted_images,
                            image_index,
                        )
                        equation_parts.append({**part, "asset_url": asset_url})
                    else:
                        equation_parts.append(part)

                file_name = render_equation_svg(equation_parts, asset_dir, image_index)
                blocks.append(
                    Block(
                        type="image",
                        order=order,
                        image_url=image_url(document_id, file_name),
                    )
                )
                order += 1
                image_index += 1
                index += 1
                continue

            if token.media:
                group: list[ParagraphToken] = [token]
                end_index = index

                if not token.text:
                    scan_index = index + 1
                    while scan_index < len(tokens):
                        candidate = tokens[scan_index]
                        if not isinstance(candidate, ParagraphToken):
                            break
                        if candidate.text or not candidate.media:
                            break
                        group.append(candidate)
                        end_index = scan_index
                        scan_index += 1

                asset_url_rows: list[list[str]] = []
                media_rows: list[list[dict]] = []
                for group_token in group:
                    row_urls: list[str] = []
                    row_media: list[dict] = []
                    for media_item in group_token.media:
                        asset_url, image_index = extract_image_asset(
                            group_token.paragraph,
                            media_item,
                            asset_dir,
                            document_id,
                            extracted_images,
                            image_index,
                        )
                        row_urls.append(asset_url)
                        row_media.append(media_item)
                    if row_urls:
                        asset_url_rows.append(row_urls)
                        media_rows.append(row_media)

                caption_text, caption_index, caption_position = find_caption_for_range(
                    tokens,
                    index,
                    end_index,
                    prefer_next=True,
                )
                if caption_index is not None:
                    consumed_caption_indexes.add(caption_index)

                total_media_count = sum(len(row) for row in asset_url_rows)
                if total_media_count == 1:
                    final_url = asset_url_rows[0][0]
                else:
                    file_name = render_composite_figure_svg(media_rows, asset_url_rows, asset_dir, image_index)
                    final_url = image_url(document_id, file_name)
                    image_index += 1

                blocks.append(
                    Block(
                        type="image",
                        order=order,
                        image_url=final_url,
                        caption=caption_text,
                        caption_position=caption_position,
                    )
                )
                order += 1

                if token.text and not is_caption_text(token.text, token.style_name):
                    block_type, level = paragraph_block_type(token.style_name)
                    blocks.append(
                        Block(
                            type=block_type,
                            order=order,
                            text=token.text,
                            level=level,
                        )
                    )
                    order += 1
                index = end_index + 1
                continue

            if token.text:
                if is_caption_text(token.text, token.style_name):
                    index += 1
                    continue
                block_type, level = paragraph_block_type(token.style_name)
                blocks.append(
                    Block(
                        type=block_type,
                        order=order,
                        text=token.text,
                        level=level,
                    )
                )
                order += 1
            index += 1

        else:
            caption_text, caption_index, caption_position = find_caption(tokens, index, prefer_next=False)
            if caption_index is not None:
                consumed_caption_indexes.add(caption_index)

            file_name = render_table_svg(token.table, asset_dir, image_index)
            blocks.append(
                Block(
                    type="image",
                    order=order,
                    image_url=image_url(document_id, file_name),
                    caption=caption_text,
                    caption_position=caption_position,
                )
            )
            order += 1
            image_index += 1
            index += 1

    if not blocks:
        blocks.append(Block(type="paragraph", order=0, text=""))

    return blocks
