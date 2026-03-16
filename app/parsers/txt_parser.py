from __future__ import annotations

import re
from pathlib import Path

from ..models import Block
from .base import normalize_text


def parse_txt(file_path: Path) -> list[Block]:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    parts = [normalize_text(part) for part in re.split(r"\n\s*\n", text) if normalize_text(part)]
    blocks: list[Block] = []
    for index, part in enumerate(parts):
        blocks.append(Block(type="paragraph", order=index, text=part))
    if not blocks:
        blocks.append(Block(type="paragraph", order=0, text=""))
    return blocks

