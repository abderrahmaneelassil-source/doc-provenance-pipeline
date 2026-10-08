"""The shared layout vocabulary: lines, blocks and pages.

Native PDF text and OCR output are both reduced to these types, so heading
detection, caption matching and table suppression work identically whether a
page had an embedded text layer or had to be read off an image.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import median

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Line:
    """One line of text with the geometry needed to reason about layout."""

    text: str
    bbox: BBox
    size: float
    """Font size in points, or for OCR the measured line height as a proxy."""
    bold: bool = False

    @property
    def top(self) -> float:
        return self.bbox[1]

    @property
    def bottom(self) -> float:
        return self.bbox[3]


@dataclass(frozen=True)
class Block:
    """A paragraph-like run of lines. One block becomes at most one unit."""

    lines: tuple[Line, ...]
    bbox: BBox

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines).strip()

    @property
    def top(self) -> float:
        return self.bbox[1]

    @property
    def bottom(self) -> float:
        return self.bbox[3]


@dataclass
class PageText:
    """Everything extracted from one page of one file."""

    page_number: int
    """1-based, as a reader would cite it."""

    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    from_ocr: bool = False
    ocr_confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)

    @property
    def char_count(self) -> int:
        """Non-whitespace-stripped character count — the sparse-page measure."""
        return len(self.text.strip())


def sorted_lines(page: PageText) -> list[Line]:
    """Every line on the page in reading order."""
    return sorted(
        (line for block in page.blocks for line in block.lines),
        key=lambda line: (line.top, line.bbox[0]),
    )


def extend_caption(
    page: PageText,
    first: Line,
    is_new_caption: Callable[[str], bool],
    max_lines: int = 6,
    gap_factor: float = 1.8,
) -> str:
    """Join a caption's continuation lines to its first line.

    Captions in these reports wrap: *"Figure 3.  Climatic water deficit (CWD)
    accumulated through water-"* continues on the next line, and returning only
    the matched line truncates mid-word.

    Continuation lines are taken **from the caption's own column**. Walking the
    page in pure reading order does not work on a multi-column layout: the next
    line down is the neighbouring column's, and the caption ends up spliced
    together with unrelated body text.
    """
    all_lines = sorted_lines(page)
    heights = [line.bottom - line.top for line in all_lines if line.bottom > line.top]
    leading = median(heights) if heights else 10.0

    # Same column means starting at roughly the same left edge. The tolerance
    # allows a modest hanging indent without reaching the next column.
    x_tolerance = max(leading * 1.5, 18.0)
    column = [
        line for line in all_lines if abs(line.bbox[0] - first.bbox[0]) <= x_tolerance
    ]
    try:
        start = column.index(first)
    except ValueError:
        return first.text

    parts = [first.text]
    previous = first
    for line in column[start + 1 : start + 1 + max_lines]:
        if (line.top - previous.bottom) > leading * gap_factor:
            break
        if is_new_caption(line.text):
            break
        parts.append(line.text)
        previous = line

    # A line ending in a hyphen is joined without a space, but the hyphen is
    # kept. It is ambiguous — a soft break inside "concen-tration", or the real
    # hyphen of "water-year" falling at the line end — and the project's rule is
    # to preserve the publisher's wording rather than guess which it is.
    joined = parts[0]
    for part in parts[1:]:
        joined = joined + part if joined.endswith("-") else f"{joined} {part}"
    return joined.strip()


def overlap_fraction(inner: BBox, outer: BBox) -> float:
    """Fraction of ``inner``'s area that lies inside ``outer``."""
    ix0 = max(inner[0], outer[0])
    iy0 = max(inner[1], outer[1])
    ix1 = min(inner[2], outer[2])
    iy1 = min(inner[3], outer[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if inner_area <= 0:
        return 0.0
    return ((ix1 - ix0) * (iy1 - iy0)) / inner_area
