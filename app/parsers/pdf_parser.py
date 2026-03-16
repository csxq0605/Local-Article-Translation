from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz

from ..models import Block
from .base import image_url, normalize_text


BBox = tuple[float, float, float, float]

CAPTION_PREFIX_RE = re.compile(
    r"^(fig(?:ure)?\.?|table)\s*((?:\d+[a-z]?|[ivxlcdm]+))\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)
EQUATION_NUMBER_RE = re.compile(r"\((?:[A-Za-z]?\d+(?:\.\d+)?)\)")
MATH_TOKEN_RE = re.compile(
    r"(?:=|≤|≥|≠|≈|∂|∇|λ|α|β|ρ|μ|π|σ|ω|τ|Δ|×|÷|"
    r"\b(?:sin|cos|tan|exp|log|max|min)\b)",
    re.IGNORECASE,
)
BRACE_TOKEN_RE = re.compile(r"[⎧⎨⎩⎪⎫⎬⎭⎮⎯⎰⎱]")
METADATA_RE = re.compile(
    r"^(received|revision received|accepted for publication|published online|copyright|"
    r"all requests for copying|graduate student|professor|corresponding author|downloaded by|"
    r"https?://doi\.org/|doi:)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class TextItem:
    id: int
    bbox: BBox
    text: str
    font_size: float

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(slots=True)
class MediaItem:
    bbox: BBox
    kind: str
    page: int
    caption: str | None = None
    caption_item_id: int | None = None
    caption_position: str | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


def overlaps(box_a: BBox, box_b: BBox) -> bool:
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    if ax1 <= bx0 or bx1 <= ax0:
        return False
    if ay1 <= by0 or by1 <= ay0:
        return False
    return True


def overlaps_any(box: BBox, boxes: Iterable[BBox]) -> bool:
    return any(overlaps(box, candidate) for candidate in boxes)


def union_boxes(boxes: Iterable[BBox]) -> BBox | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def horizontal_overlap_ratio(box_a: BBox, box_b: BBox) -> float:
    overlap = max(0.0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0]))
    min_width = max(1.0, min(box_a[2] - box_a[0], box_b[2] - box_b[0]))
    return overlap / min_width


def vertical_overlap_ratio(box_a: BBox, box_b: BBox) -> float:
    overlap = max(0.0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1]))
    min_height = max(1.0, min(box_a[3] - box_a[1], box_b[3] - box_b[1]))
    return overlap / min_height


def horizontal_gap_between(box_a: BBox, box_b: BBox) -> float:
    if box_a[2] < box_b[0]:
        return box_b[0] - box_a[2]
    if box_b[2] < box_a[0]:
        return box_a[0] - box_b[2]
    return 0.0


def vertical_gap(upper: BBox, lower: BBox) -> float:
    return lower[1] - upper[3]


def normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_metadata_text(item: TextItem) -> bool:
    compact = normalize_match_text(item.text)
    return bool(METADATA_RE.match(compact))


def is_caption_kind(item: TextItem) -> str | None:
    compact = normalize_match_text(item.text)
    match = CAPTION_PREFIX_RE.match(compact)
    if match is None:
        return None

    label = match.group(1).lower()
    tail = match.group(3).lstrip()
    if tail:
        if tail[0] not in "|:.;,(":
            first_word_match = re.match(r"([A-Za-z]+)", tail)
            if first_word_match and first_word_match.group(1)[0].islower():
                return None

    if label.startswith("table"):
        return "table"
    return "figure"


def is_paragraph_like(item: TextItem, page_width: float) -> bool:
    compact = normalize_match_text(item.text)
    if is_caption_kind(item) or is_metadata_text(item):
        return False
    if is_tabular_text(item):
        return False
    return (
        item.width >= page_width * 0.28
        and len(compact) >= 80
        and item.font_size <= 12.5
    )


def is_tabular_text(item: TextItem) -> bool:
    compact = normalize_match_text(item.text)
    lines = [line.strip() for line in item.text.split("\n") if line.strip()]
    if len(lines) < 3 or not any(char.isdigit() for char in compact):
        return False
    average_line_length = sum(len(line) for line in lines) / len(lines)
    return (
        len(compact) >= 24
        and (
            compact.count("|") >= 4
            or average_line_length <= 26
        )
    )


def is_margin_text(item: TextItem, page_width: float, page_height: float) -> bool:
    if item.x1 <= page_width * 0.03 or item.x0 >= page_width * 0.97:
        return True
    if item.y1 <= page_height * 0.055:
        return True
    if item.y0 >= page_height * 0.965:
        return True
    return False


def is_equation_number_text(item: TextItem) -> bool:
    compact = normalize_match_text(item.text)
    return len(compact) <= 24 and bool(EQUATION_NUMBER_RE.search(compact))


def equation_complexity_score(text: str) -> int:
    compact = normalize_match_text(text)
    lines = [normalize_match_text(line) for line in text.splitlines() if normalize_match_text(line)]
    score = len(MATH_TOKEN_RE.findall(compact))
    score += compact.count("/")
    score += compact.count("{") + compact.count("}")
    score += compact.count("[") + compact.count("]")
    score += compact.count("_")
    score += compact.count(";")

    symbol_chars = sum(
        1
        for char in compact
        if not char.isalnum() and not char.isspace() and char not in ",.;:"
    )
    score += min(6, symbol_chars // 3)

    if BRACE_TOKEN_RE.search(compact):
        score += 2
    if re.search(r"\b[a-zA-Z]\s*[A-Za-z0-9]*\s*=", compact):
        score += 2
    if re.search(r"\b[xyf]\s+[A-Za-z0-9]", compact, re.IGNORECASE):
        score += 2
    if len(lines) >= 2 and any(any(ch.isdigit() for ch in line) for line in lines):
        score += 1
    if len(lines) >= 2 and compact.count(";") >= 1:
        score += 1
    return score


def is_equation_component(item: TextItem, page_width: float) -> bool:
    compact = normalize_match_text(item.text)
    lines = [normalize_match_text(line) for line in item.text.splitlines() if normalize_match_text(line)]
    if not compact:
        return False
    score = equation_complexity_score(compact)
    if is_caption_kind(item) or is_metadata_text(item):
        return False
    if is_paragraph_like(item, page_width):
        return False
    if is_equation_number_text(item):
        return True
    if BRACE_TOKEN_RE.search(compact):
        return True
    if is_tabular_text(item) and score < 3:
        return False
    if item.width > page_width * 0.86 or len(compact) > 220:
        return False
    if score >= 2 and len(lines) >= 2 and is_centered_equation_item(item, page_width):
        return True
    return score >= 3


def is_centered_equation_item(item: TextItem, page_width: float) -> bool:
    return abs(item.center_x - page_width / 2) <= page_width * 0.18


def is_header_artifact_media(bbox: BBox, page_width: float, page_height: float) -> bool:
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if bbox[3] <= page_height * 0.09 and width <= page_width * 0.2 and height <= page_height * 0.1:
        return True
    return False


def clip_to_page(page: fitz.Page, bbox: BBox, padding: float = 6.0) -> fitz.Rect:
    rect = fitz.Rect(
        max(page.rect.x0, bbox[0] - padding),
        max(page.rect.y0, bbox[1] - padding),
        min(page.rect.x1, bbox[2] + padding),
        min(page.rect.y1, bbox[3] + padding),
    )
    return rect


def save_region_image(
    *,
    page: fitz.Page,
    bbox: BBox,
    asset_dir: Path,
    document_id: str,
    image_index: int,
    prefix: str,
) -> tuple[str, int]:
    rect = clip_to_page(page, bbox)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    file_name = f"pdf-page-{page.number + 1:04d}-{prefix}-{image_index:04d}.png"
    pixmap.save(asset_dir / file_name)
    return image_url(document_id, file_name), image_index + 1


def extract_text_items(page: fitz.Page) -> list[TextItem]:
    text_dict = page.get_text("dict")
    items: list[TextItem] = []
    next_id = 0
    for raw_block in text_dict.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        lines: list[str] = []
        max_size = 0.0
        for line in raw_block.get("lines", []):
            spans: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text:
                    spans.append(text)
                    max_size = max(max_size, float(span.get("size", 0.0)))
            if spans:
                lines.append(" ".join(spans))
        text = normalize_text("\n".join(lines))
        if not text:
            continue
        items.append(
            TextItem(
                id=next_id,
                bbox=tuple(raw_block["bbox"]),
                text=text,
                font_size=max_size,
            )
        )
        next_id += 1
    return items


def match_caption_to_media(
    media_item: MediaItem,
    caption_items: list[TextItem],
    *,
    preferred_kind: str,
    prefer_below: bool,
    used_caption_ids: set[int],
    page_width: float,
) -> None:
    best_score: tuple[float, float, float] | None = None
    best_item: TextItem | None = None
    best_position = media_item.caption_position

    for item in caption_items:
        if item.id in used_caption_ids:
            continue
        if is_caption_kind(item) != preferred_kind:
            continue

        overlap = horizontal_overlap_ratio(media_item.bbox, item.bbox)
        center_distance = abs(item.center_x - ((media_item.x0 + media_item.x1) / 2))
        vertical_overlap = vertical_overlap_ratio(media_item.bbox, item.bbox)

        if prefer_below:
            below_gap = vertical_gap(media_item.bbox, item.bbox)
            if 0 <= below_gap <= 110 and (overlap >= 0.15 or center_distance <= page_width * 0.22):
                score = (0, below_gap, -overlap)
                caption_position = "below"
            else:
                above_gap = vertical_gap(item.bbox, media_item.bbox)
                if 0 <= above_gap <= 90 and (overlap >= 0.15 or center_distance <= page_width * 0.22):
                    score = (1, above_gap, -overlap)
                    caption_position = "above"
                else:
                    right_gap = item.x0 - media_item.x1
                    left_gap = media_item.x0 - item.x1
                    if 0 <= right_gap <= 72 and vertical_overlap >= 0.35:
                        score = (2, right_gap, -vertical_overlap)
                        caption_position = "right"
                    elif 0 <= left_gap <= 72 and vertical_overlap >= 0.35:
                        score = (2, left_gap, -vertical_overlap)
                        caption_position = "left"
                    else:
                        continue
        else:
            above_gap = vertical_gap(item.bbox, media_item.bbox)
            if 0 <= above_gap <= 110 and (overlap >= 0.15 or center_distance <= page_width * 0.22):
                score = (0, above_gap, -overlap)
                caption_position = "above"
            else:
                below_gap = vertical_gap(media_item.bbox, item.bbox)
                if 0 <= below_gap <= 90 and (overlap >= 0.15 or center_distance <= page_width * 0.22):
                    score = (1, below_gap, -overlap)
                    caption_position = "below"
                else:
                    right_gap = item.x0 - media_item.x1
                    left_gap = media_item.x0 - item.x1
                    if 0 <= right_gap <= 72 and vertical_overlap >= 0.35:
                        score = (2, right_gap, -vertical_overlap)
                        caption_position = "right"
                    elif 0 <= left_gap <= 72 and vertical_overlap >= 0.35:
                        score = (2, left_gap, -vertical_overlap)
                        caption_position = "left"
                    else:
                        continue

        if best_score is None or score < best_score:
            best_score = score
            best_item = item
            best_position = caption_position

    if best_item is not None:
        media_item.caption = best_item.text
        media_item.caption_item_id = best_item.id
        media_item.caption_position = best_position
        used_caption_ids.add(best_item.id)


def infer_column_bounds(caption: TextItem, page_width: float) -> tuple[float, float]:
    center = caption.center_x
    gutter = page_width * 0.06
    midpoint = page_width / 2
    if caption.width >= page_width * 0.58 or (
        caption.width >= page_width * 0.42 and abs(center - midpoint) <= page_width * 0.08
    ):
        return (page_width * 0.05, page_width * 0.95)
    if center < midpoint:
        return (page_width * 0.05, midpoint + gutter)
    return (midpoint - gutter, page_width * 0.95)


def infer_figure_bbox(
    *,
    page: fitz.Page,
    caption: TextItem,
    text_items: list[TextItem],
    occupied_boxes: list[BBox],
    page_width: float,
    page_height: float,
) -> BBox | None:
    x_min, x_max = infer_column_bounds(caption, page_width)
    paragraph_stop = page_height * 0.055
    for item in text_items:
        if item.id == caption.id:
            continue
        if item.y1 >= caption.y0:
            continue
        if item.x1 <= x_min or item.x0 >= x_max:
            continue
        if is_paragraph_like(item, page_width):
            paragraph_stop = max(paragraph_stop, item.y1 + 2)

    candidate_boxes: list[BBox] = []

    for item in text_items:
        if item.id == caption.id:
            continue
        if item.y1 > caption.y0 or item.y0 < paragraph_stop:
            continue
        if item.x1 <= x_min or item.x0 >= x_max:
            continue
        if overlaps_any(item.bbox, occupied_boxes):
            continue
        if is_paragraph_like(item, page_width) or is_metadata_text(item) or is_caption_kind(item):
            continue
        candidate_boxes.append(item.bbox)

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        box = (rect.x0, rect.y0, rect.x1, rect.y1)
        if box[3] > caption.y0 or box[1] < paragraph_stop:
            continue
        if box[2] <= x_min or box[0] >= x_max:
            continue
        if overlaps_any(box, occupied_boxes):
            continue
        candidate_boxes.append(box)

    bbox = union_boxes(candidate_boxes)
    if bbox is None:
        return None

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width < page_width * 0.14 or height < 24:
        return None
    return bbox


def infer_table_bbox(
    *,
    page: fitz.Page,
    caption: TextItem,
    text_items: list[TextItem],
    occupied_boxes: list[BBox],
    page_width: float,
    page_height: float,
) -> BBox | None:
    x_min, x_max = infer_column_bounds(caption, page_width)
    paragraph_stop = page_height * 0.965
    for item in text_items:
        if item.id == caption.id:
            continue
        if item.y0 <= caption.y1:
            continue
        if item.x1 <= x_min or item.x0 >= x_max:
            continue
        if is_paragraph_like(item, page_width):
            paragraph_stop = min(paragraph_stop, item.y0 - 2)

    candidate_boxes: list[BBox] = []

    for item in text_items:
        if item.id == caption.id:
            continue
        if item.y0 < caption.y1 or item.y1 > paragraph_stop:
            continue
        if item.x1 <= x_min or item.x0 >= x_max:
            continue
        if overlaps_any(item.bbox, occupied_boxes):
            continue
        if is_metadata_text(item) or is_caption_kind(item):
            continue
        if is_tabular_text(item) or item.font_size <= 8.5:
            candidate_boxes.append(item.bbox)

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        box = (rect.x0, rect.y0, rect.x1, rect.y1)
        if box[1] < caption.y1 or box[3] > paragraph_stop:
            continue
        if box[2] <= x_min or box[0] >= x_max:
            continue
        if overlaps_any(box, occupied_boxes):
            continue
        candidate_boxes.append(box)

    bbox = union_boxes(candidate_boxes)
    if bbox is None:
        return None

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width < page_width * 0.14 or height < 18:
        return None
    return bbox


def is_full_width_item(item, page_width: float) -> bool:
    return (item.x1 - item.x0) >= page_width * 0.58 or (
        item.x0 < page_width * 0.46 and item.x1 > page_width * 0.54
    )


def sort_column_items(items: list, page_width: float) -> list:
    midpoint = page_width / 2
    gutter = page_width * 0.05
    left_items: list = []
    right_items: list = []
    center_items: list = []

    for item in items:
        if item.x1 <= midpoint + gutter and item.center_x < midpoint:
            left_items.append(item)
        elif item.x0 >= midpoint - gutter and item.center_x >= midpoint:
            right_items.append(item)
        else:
            center_items.append(item)

    left_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))
    right_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))
    center_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))

    if left_items and right_items:
        return center_items + left_items + right_items

    merged_items = center_items + left_items + right_items
    merged_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))
    return merged_items


def sort_layout_items(items: list, page_width: float) -> list:
    full_width_items = [item for item in items if is_full_width_item(item, page_width)]
    column_items = [item for item in items if item not in full_width_items]
    full_width_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))

    ordered: list = []
    remaining_column_items = list(column_items)
    previous_cutoff = float("-inf")

    for full_item in full_width_items:
        before_segment = [
            item
            for item in remaining_column_items
            if previous_cutoff <= item.y0 < full_item.y0
        ]
        remaining_column_items = [item for item in remaining_column_items if item not in before_segment]
        ordered.extend(sort_column_items(before_segment, page_width))
        ordered.append(full_item)
        previous_cutoff = max(previous_cutoff, full_item.y1)

    trailing_segment = [item for item in remaining_column_items if item.y0 >= previous_cutoff]
    leading_segment = [item for item in remaining_column_items if item.y0 < previous_cutoff]
    ordered.extend(sort_column_items(leading_segment, page_width))
    ordered.extend(sort_column_items(trailing_segment, page_width))
    return ordered


def split_first_page_preamble(items: list[TextItem], page_width: float) -> tuple[list[TextItem], list[TextItem]]:
    body_starts = [
        item.y0
        for item in items
        if is_paragraph_like(item, page_width)
        and item.width <= page_width * 0.48
        and (item.x0 <= page_width * 0.12 or item.x1 >= page_width * 0.88)
    ]
    if not body_starts:
        return items, []

    body_start_y = min(body_starts)
    preamble = [item for item in items if item.y0 < body_start_y]
    body = [item for item in items if item.y0 >= body_start_y]
    return preamble, body


def is_equation_support_box(box: BBox, equation_bbox: BBox, page_width: float, page_height: float) -> bool:
    width = box[2] - box[0]
    height = box[3] - box[1]
    if width < 4 or height < 4:
        return False
    if width > page_width * 0.9 or height > page_height * 0.25:
        return False

    expanded_bbox = (
        equation_bbox[0] - 40,
        equation_bbox[1] - 24,
        equation_bbox[2] + 40,
        equation_bbox[3] + 24,
    )
    return overlaps(box, expanded_bbox)


def expand_equation_bbox(
    *,
    page: fitz.Page,
    bbox: BBox,
    occupied_boxes: list[BBox],
    page_width: float,
    page_height: float,
) -> BBox:
    support_boxes: list[BBox] = [bbox]

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        box = (rect.x0, rect.y0, rect.x1, rect.y1)
        if overlaps_any(box, occupied_boxes):
            continue
        if is_equation_support_box(box, bbox, page_width, page_height):
            support_boxes.append(box)

    seen_xrefs: set[int] = set()
    for image in page.get_images(full=True):
        xref = image[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        for rect in page.get_image_rects(xref):
            box = tuple(rect)
            if overlaps_any(box, occupied_boxes):
                continue
            if is_equation_support_box(box, bbox, page_width, page_height):
                support_boxes.append(box)

    return union_boxes(support_boxes) or bbox


def detect_equation_media_items(
    *,
    page: fitz.Page,
    text_items: list[TextItem],
    occupied_boxes: list[BBox],
    page_width: float,
    page_height: float,
    page_number: int,
) -> tuple[list[MediaItem], set[int]]:
    candidates = [
        item
        for item in text_items
        if not overlaps_any(item.bbox, occupied_boxes)
        and is_equation_component(item, page_width)
    ]
    candidates.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))

    by_id = {item.id: item for item in candidates}
    remaining_ids = {item.id for item in candidates}
    equation_items: list[MediaItem] = []
    consumed_ids: set[int] = set()

    while remaining_ids:
        current_id = min(remaining_ids, key=lambda item_id: (round(by_id[item_id].y0, 1), round(by_id[item_id].x0, 1)))
        group_ids = {current_id}
        queue = [current_id]
        remaining_ids.remove(current_id)

        while queue:
            anchor = by_id[queue.pop()]
            for other_id in list(remaining_ids):
                other = by_id[other_id]
                stacked_down = 0 <= other.y0 - anchor.y1 <= 34 and horizontal_overlap_ratio(anchor.bbox, other.bbox) >= 0.15
                stacked_up = 0 <= anchor.y0 - other.y1 <= 34 and horizontal_overlap_ratio(anchor.bbox, other.bbox) >= 0.15
                side_join = vertical_overlap_ratio(anchor.bbox, other.bbox) >= 0.45 and horizontal_gap_between(anchor.bbox, other.bbox) <= 80
                if stacked_down or stacked_up or side_join:
                    remaining_ids.remove(other_id)
                    group_ids.add(other_id)
                    queue.append(other_id)

        group = [by_id[item_id] for item_id in group_ids]
        has_formula_core = any(
            not is_equation_number_text(item) and equation_complexity_score(item.text) >= 3
            for item in group
        )
        if not has_formula_core:
            continue
        if len(group) == 1 and not is_equation_number_text(group[0]):
            only_item = group[0]
            if equation_complexity_score(only_item.text) < 4 or not is_centered_equation_item(only_item, page_width):
                continue

        bbox = union_boxes(item.bbox for item in group)
        if bbox is None:
            continue
        if (bbox[2] - bbox[0]) < 80 or (bbox[3] - bbox[1]) < 18:
            continue
        bbox = expand_equation_bbox(
            page=page,
            bbox=bbox,
            occupied_boxes=occupied_boxes,
            page_width=page_width,
            page_height=page_height,
        )

        equation_items.append(MediaItem(bbox=bbox, kind="equation", page=page_number))
        consumed_ids.update(group_ids)

    return equation_items, consumed_ids


def make_text_block(item: TextItem, order: int, page_number: int, heading_font_size: float) -> Block:
    block_type = "heading" if item.font_size >= heading_font_size and item.width >= 220 else "paragraph"
    level = 1 if block_type == "heading" else None
    return Block(
        type=block_type,
        order=order,
        page=page_number,
        text=item.text,
        level=level,
    )


def parse_pdf(file_path: Path, asset_dir: Path, document_id: str) -> list[Block]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    pdf = fitz.open(file_path)

    blocks: list[Block] = []
    order = 0
    image_index = 0

    for page_number, page in enumerate(pdf, start=1):
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        text_items = extract_text_items(page)
        text_items = [
            item
            for item in text_items
            if not is_margin_text(item, page_width, page_height)
            and not is_metadata_text(item)
        ]

        table_media_items: list[MediaItem] = []
        image_media_items: list[MediaItem] = []
        used_caption_ids: set[int] = set()

        if hasattr(page, "find_tables"):
            try:
                finder = page.find_tables()
                tables = getattr(finder, "tables", finder) or []
            except Exception:
                tables = []
            for table in tables:
                bbox = tuple(table.bbox)
                if is_header_artifact_media(bbox, page_width, page_height):
                    continue
                table_media_items.append(MediaItem(bbox=bbox, kind="table", page=page_number))

        seen_xrefs: set[int] = set()
        for image in page.get_images(full=True):
            xref = image[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            bbox = tuple(rects[0])
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            if width * height < 1600:
                continue
            if is_header_artifact_media(bbox, page_width, page_height):
                continue
            image_media_items.append(MediaItem(bbox=bbox, kind="image", page=page_number))

        caption_items = [item for item in text_items if is_caption_kind(item)]

        for media_item in table_media_items:
            match_caption_to_media(
                media_item,
                caption_items,
                preferred_kind="table",
                prefer_below=False,
                used_caption_ids=used_caption_ids,
                page_width=page_width,
            )
        for media_item in image_media_items:
            match_caption_to_media(
                media_item,
                caption_items,
                preferred_kind="figure",
                prefer_below=True,
                used_caption_ids=used_caption_ids,
                page_width=page_width,
            )

        media_items = [item for item in table_media_items if item.caption] + [item for item in image_media_items if item.caption]
        occupied_boxes: list[BBox] = [item.bbox for item in media_items]

        for caption_item in caption_items:
            if caption_item.id in used_caption_ids:
                continue
            if is_caption_kind(caption_item) != "table":
                continue
            table_bbox = infer_table_bbox(
                page=page,
                caption=caption_item,
                text_items=text_items,
                occupied_boxes=occupied_boxes,
                page_width=page_width,
                page_height=page_height,
            )
            if table_bbox is None:
                continue
            media_item = MediaItem(
                bbox=table_bbox,
                kind="table",
                page=page_number,
                caption=caption_item.text,
                caption_item_id=caption_item.id,
                caption_position="above",
            )
            media_items.append(media_item)
            occupied_boxes.append(table_bbox)
            used_caption_ids.add(caption_item.id)

        for caption_item in caption_items:
            if caption_item.id in used_caption_ids:
                continue
            if is_caption_kind(caption_item) != "figure":
                continue
            figure_bbox = infer_figure_bbox(
                page=page,
                caption=caption_item,
                text_items=text_items,
                occupied_boxes=occupied_boxes,
                page_width=page_width,
                page_height=page_height,
            )
            if figure_bbox is None:
                continue
            media_item = MediaItem(
                bbox=figure_bbox,
                kind="figure",
                page=page_number,
                caption=caption_item.text,
                caption_item_id=caption_item.id,
                caption_position="below",
            )
            media_items.append(media_item)
            occupied_boxes.append(figure_bbox)
            used_caption_ids.add(caption_item.id)

        equation_media_items, equation_item_ids = detect_equation_media_items(
            page=page,
            text_items=text_items,
            occupied_boxes=occupied_boxes,
            page_width=page_width,
            page_height=page_height,
            page_number=page_number,
        )
        media_items.extend(equation_media_items)
        occupied_boxes.extend(item.bbox for item in equation_media_items)

        media_items.sort(key=lambda item: (round(item.y0, 1), round(item.x0, 1)))
        body_candidates = [
            item
            for item in text_items
            if item.id not in used_caption_ids
            and item.id not in equation_item_ids
            and not overlaps_any(item.bbox, occupied_boxes)
        ]

        def append_media_block(media_item: MediaItem) -> None:
            nonlocal order, image_index
            url, image_index_local = save_region_image(
                page=page,
                bbox=media_item.bbox,
                asset_dir=asset_dir,
                document_id=document_id,
                image_index=image_index,
                prefix=media_item.kind,
            )
            image_index = image_index_local
            blocks.append(
                Block(
                    type="image",
                    order=order,
                    page=page_number,
                    image_url=url,
                    caption=media_item.caption,
                    caption_position=media_item.caption_position,
                )
            )
            order += 1

        if page_number > 1:
            blocks.append(
                Block(
                    type="page_break",
                    order=order,
                    page=page_number,
                    text=f"Page {page_number}",
                )
            )
            order += 1

        if page_number == 1:
            preamble_items, page_body_items = split_first_page_preamble(body_candidates, page_width)
            first_body_y = min((item.y0 for item in page_body_items), default=float("inf"))
            preamble_media_items = [item for item in media_items if item.y0 < first_body_y]
            page_media_items = [item for item in media_items if item.y0 >= first_body_y]
            max_font_size = max((item.font_size for item in preamble_items), default=0.0)
            heading_threshold = max_font_size - 0.5
            preamble_layout = sorted(
                [*preamble_items, *preamble_media_items],
                key=lambda item: (round(item.y0, 1), round(item.x0, 1)),
            )
            for item in preamble_layout:
                if isinstance(item, TextItem):
                    blocks.append(
                        make_text_block(
                            item,
                            order=order,
                            page_number=page_number,
                            heading_font_size=heading_threshold,
                        )
                    )
                    order += 1
                else:
                    append_media_block(item)
        else:
            page_body_items = body_candidates
            page_media_items = media_items

        ordered_page_items = sort_layout_items([*page_body_items, *page_media_items], page_width)
        for item in ordered_page_items:
            if isinstance(item, TextItem):
                blocks.append(
                    Block(
                        type="paragraph",
                        order=order,
                        page=page_number,
                        text=item.text,
                    )
                )
                order += 1
            else:
                append_media_block(item)

    if not blocks:
        blocks.append(Block(type="paragraph", order=0, text=""))
    return blocks
