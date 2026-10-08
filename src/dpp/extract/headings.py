"""Heading detection, so every passage knows its nearest heading.

Headings are inferred from what a reader actually uses: a larger or bolder line,
short, and not a sentence. The nearest preceding heading carries forward across
pages, because a section does not restart at a page break.

Captions ("Figure 3.", "Table 7.") are explicitly *not* headings — they belong to
the figure or table they label, and are attached there instead.
"""

from __future__ import annotations

import re

from ..config import Config
from .layout import Block, Line, PageText

#: Lines opening with one of these label a float, not a section.
_CAPTION_PREFIX = re.compile(
    r"^\s*(figure|fig\.?|table|plate|chart|exhibit|appendix table)\s*[\d IVXLC]", re.IGNORECASE
)

#: A line that is mostly digits and punctuation is a page number or a table row.
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def is_caption_line(text: str) -> bool:
    return bool(_CAPTION_PREFIX.match(text))


def is_heading_line(line: Line, body_size: float, config: Config) -> bool:
    """Whether one line reads as a section heading."""
    text = line.text.strip()
    if not text or len(text) > config.heading_max_chars:
        return False
    if is_caption_line(text):
        return False

    words = _WORD.findall(text)
    if not words:
        # Pure numbers or symbols: page furniture, never a heading.
        return False
    # Running text that happens to be short still ends like a sentence.
    if text.endswith((".", ";", ",")) and len(words) > 6:
        return False

    if body_size > 0 and line.size >= body_size * config.heading_size_ratio:
        return True
    if line.bold and (body_size <= 0 or line.size >= body_size * 0.98):
        return True
    # Small-caps/all-caps headings are common in older government reports, where
    # the typesetter changed case rather than size.
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(words) <= 12:
        return True
    return False


def block_heading(block: Block, body_size: float, config: Config) -> str | None:
    """If the whole block is a heading, return its text.

    A block only counts when *every* line in it is heading-like, which stops a
    bold lead-in from turning a whole paragraph into a heading.
    """
    if not block.lines or len(block.lines) > 3:
        return None
    if all(is_heading_line(line, body_size, config) for line in block.lines):
        return block.text or None
    return None


def assign_headings(
    pages: list[PageText], body_size: float, config: Config, carry: str | None = None
) -> dict[int, dict[int, str | None]]:
    """Map ``{page_number: {block_index: nearest_heading}}``.

    ``carry`` seeds the current heading, so a document processed page-range by
    page-range keeps its section context.
    """
    current = carry
    assigned: dict[int, dict[int, str | None]] = {}

    for page in pages:
        per_block: dict[int, str | None] = {}
        for index, block in enumerate(page.blocks):
            heading = block_heading(block, body_size, config)
            if heading:
                current = heading
            per_block[index] = current
        assigned[page.page_number] = per_block

    return assigned


def count_headings(
    pages: list[PageText], body_size: float, config: Config
) -> int:
    """How many distinct headings were found. Used for the ledger status."""
    found = {
        heading
        for page in pages
        for block in page.blocks
        if (heading := block_heading(block, body_size, config))
    }
    return len(found)
