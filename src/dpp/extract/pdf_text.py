"""Per-page text extraction with PyMuPDF, plus sparse-page detection.

A *sparse* page is one whose embedded text layer yields almost nothing. That is
the signal that the page is an image — a scan, a map, a full-page plate — and the
only pages the pipeline is willing to spend OCR time on.

Sparse-page detection is deliberately a pure function of the character count and
the threshold, so it can be tested without a PDF.

A second concern is *shredded* text layers. Some scanned documents (the 1953
NACA report in the sample set is one) carry an OCR layer where every word is its
own line: one table page reports 1,361 "lines", each a single number. Heading
detection, caption matching and table detection all reason about lines, so
:func:`repair_shredded_lines` rebuilds real lines from word geometry before any
of that runs.
"""

from __future__ import annotations

from statistics import median

import fitz

from .layout import Block, Line, PageText

#: PyMuPDF span flag bit for bold.
_FLAG_BOLD = 1 << 4

#: PyMuPDF block type for text (as opposed to image) blocks.
_BLOCK_TEXT = 0


def is_sparse(char_count: int, sparse_min_chars: int) -> bool:
    """Whether a page's extractable text is too thin to be the real content.

    The rule is a single configurable threshold on non-whitespace characters.
    ``sparse_min_chars`` of 0 disables the rule.
    """
    if sparse_min_chars <= 0:
        return False
    return char_count < sparse_min_chars


def is_shredded(page: PageText, single_word_frac: float, min_lines: int = 20) -> bool:
    """Whether this page's text layer has lost its line structure.

    True when most "lines" hold a single word. Below ``min_lines`` the page is
    too short to judge — a title page legitimately looks like this.
    """
    lines = [line for block in page.blocks for line in block.lines]
    if len(lines) < min_lines:
        return False
    singles = sum(1 for line in lines if len(line.text.split()) <= 1)
    return singles / len(lines) > single_word_frac


def repair_shredded_lines(page: PageText) -> PageText:
    """Rebuild lines and paragraphs from word geometry.

    Words sharing a baseline are joined left-to-right into one line; runs of
    lines with normal leading are grouped back into blocks. Font size and bold
    are carried over from the largest contributing fragment, so heading
    detection still has something to work with.
    """
    fragments = [line for block in page.blocks for line in block.lines]
    if not fragments:
        return page

    heights = [f.bbox[3] - f.bbox[1] for f in fragments if f.bbox[3] > f.bbox[1]]
    unit = median(heights) if heights else 1.0
    tolerance = max(unit * 0.6, 1.0)

    # --- group fragments onto shared baselines ---------------------------
    rows: list[list[Line]] = []
    for fragment in sorted(fragments, key=lambda f: ((f.bbox[1] + f.bbox[3]) / 2, f.bbox[0])):
        centre = (fragment.bbox[1] + fragment.bbox[3]) / 2
        if rows:
            last = rows[-1]
            last_centre = sum((f.bbox[1] + f.bbox[3]) / 2 for f in last) / len(last)
            if abs(centre - last_centre) <= tolerance:
                last.append(fragment)
                continue
        rows.append([fragment])

    lines: list[Line] = []
    for row in rows:
        row.sort(key=lambda f: f.bbox[0])
        widest = max(row, key=lambda f: f.size)
        lines.append(
            Line(
                text=" ".join(f.text for f in row).strip(),
                bbox=(
                    min(f.bbox[0] for f in row),
                    min(f.bbox[1] for f in row),
                    max(f.bbox[2] for f in row),
                    max(f.bbox[3] for f in row),
                ),
                size=widest.size,
                bold=any(f.bold for f in row),
            )
        )

    # --- group lines back into paragraph-like blocks ----------------------
    repaired = PageText(
        page_number=page.page_number,
        width=page.width,
        height=page.height,
        from_ocr=page.from_ocr,
        ocr_confidence=page.ocr_confidence,
    )
    current: list[Line] = []
    for line in lines:
        if current and (line.top - current[-1].bottom) > unit * 1.6:
            repaired.blocks.append(_block_of(current))
            current = []
        current.append(line)
    if current:
        repaired.blocks.append(_block_of(current))

    return repaired


def _block_of(lines: list[Line]) -> Block:
    return Block(
        lines=tuple(lines),
        bbox=(
            min(line.bbox[0] for line in lines),
            min(line.bbox[1] for line in lines),
            max(line.bbox[2] for line in lines),
            max(line.bbox[3] for line in lines),
        ),
    )


def extract_page(page: fitz.Page) -> PageText:
    """Read one page's embedded text layer into the shared layout types."""
    raw = page.get_text("dict")
    result = PageText(
        page_number=page.number + 1,
        width=float(raw.get("width") or page.rect.width),
        height=float(raw.get("height") or page.rect.height),
    )

    for raw_block in raw.get("blocks", []):
        if raw_block.get("type") != _BLOCK_TEXT:
            continue
        lines: list[Line] = []
        for raw_line in raw_block.get("lines", []):
            line = _build_line(raw_line)
            if line is not None:
                lines.append(line)
        if not lines:
            continue
        result.blocks.append(
            Block(lines=tuple(lines), bbox=tuple(float(v) for v in raw_block["bbox"]))
        )

    return result


def _build_line(raw_line: dict) -> Line | None:
    """Collapse a PyMuPDF line's spans into one :class:`Line`.

    Font size is the largest span size on the line, and the line counts as bold
    when most of its characters are: both are what a reader's eye uses to spot a
    heading, and a stray bold word should not promote a body line.
    """
    spans = raw_line.get("spans", [])
    text = "".join(span.get("text", "") for span in spans)
    if not text.strip():
        return None

    sizes = [float(span.get("size", 0.0)) for span in spans if span.get("text", "").strip()]
    bold_chars = sum(
        len(span.get("text", "")) for span in spans if int(span.get("flags", 0)) & _FLAG_BOLD
    )
    total_chars = sum(len(span.get("text", "")) for span in spans)

    return Line(
        text=text.strip(),
        bbox=tuple(float(v) for v in raw_line["bbox"]),
        size=max(sizes) if sizes else 0.0,
        bold=total_chars > 0 and bold_chars / total_chars > 0.5,
    )


def body_font_size(pages: list[PageText]) -> float:
    """The document's body text size: the character-weighted modal line size.

    Weighting by characters rather than counting lines keeps a title page or a
    run of short captions from redefining what "normal" looks like.
    """
    weights: dict[float, int] = {}
    for page in pages:
        for block in page.blocks:
            for line in block.lines:
                if line.size <= 0:
                    continue
                key = round(line.size, 1)
                weights[key] = weights.get(key, 0) + len(line.text)
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda item: (item[1], -item[0]))[0]
