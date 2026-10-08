"""Table extraction with pdfplumber, preserving headers, units and captions.

Cells are stored **exactly as written**. Nothing is normalised, re-cased or
unit-converted: a column heading that reads ``Calcium, in milligrams per liter``
or ``Temperature (°C)`` is the only place those units are recorded, and rewriting
it would silently destroy the measurement's meaning.

Each table is kept twice, because the two uses pull in opposite directions:

* :meth:`ExtractedTable.rows_json` — the document's header lines plus structured
  data rows, for a reader or a downstream consumer
* :meth:`ExtractedTable.readable_text` — a flat rendering, so FTS5 can find the
  table by its contents and the snippet shown is legible

The table's own bounding box is returned too, so the text blocks sitting inside
it can be suppressed as passages and the content is not indexed twice.

**On the detection strategy.** pdfplumber's default ``lines`` strategy keys off
ruling lines, and the publishers in the sample set mostly do not draw them:
measured across the USGS and NACA reports, ``lines`` found 0 tables on most
table pages and 2-3 rows on the rest, while ``text`` (column detection from
whitespace alignment) found the same tables with their full row counts — and ran
faster, because ``lines`` has to intersect every vector edge on pages carrying
large maps. ``text`` is therefore the default. It trades a recall gain for a
false-positive risk on aligned prose, which :func:`looks_like_table` filters out.
Set ``table_strategy="lines"`` for documents that really are ruled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from statistics import median

import pdfplumber

from ..config import Config
from .layout import BBox, Line, PageText, extend_caption

#: "Table 4.", "TABLE 4-1", "Table 4— Summary" — the caption conventions used
#: across the government reports in the sample set. The trailing punctuation is
#: required: without it, prose like "Table 1 shows that New York State..." and
#: cross-references like "(table 3)." match, and every page of running text
#: becomes a table candidate.
_TABLE_CAPTION = re.compile(r"^\s*table\s+[\dIVXLC]+\s*[.\-–—:]", re.IGNORECASE)

#: A token that reads as a measurement or a count, allowing for the signs,
#: separators, parentheses and exponents used in these reports.
_NUMERIC_TOKEN = re.compile(r"^[+\-–−(<>]?[\d.,]+[)%]?$|^[\d.,]+[eE][+\-]?\d+$")


@dataclass(frozen=True)
class TableBand:
    """The region of a page holding one table, split into header and data.

    Keeping the two apart matters twice over: the header lines are captured
    verbatim (so their units survive), and pdfplumber is asked to find columns in
    the data rows alone, where the grid is regular. Handing it the merged,
    multi-row headers as well is what fragments the columns.
    """

    bbox: BBox
    data_bbox: BBox
    header_lines: tuple[str, ...]


@dataclass(frozen=True)
class ExtractedTable:
    """One table, with its provenance and both representations."""

    page_number: int
    bbox: BBox
    header_lines: list[str]
    """The document's own column headings, **exactly as written** — this is the
    only place a table's units are recorded, so they are never re-split or
    normalised."""

    rows: list[list[str]]
    """Data rows as pdfplumber split them. Cells are verbatim."""

    caption: str | None

    @property
    def n_cols(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def header_text(self) -> str:
        return "\n".join(self.header_lines)

    def rows_json(self) -> str:
        return json.dumps(
            {"header_lines": self.header_lines, "rows": self.rows}, ensure_ascii=False
        )

    def readable_text(self) -> str:
        """Flat rendering for search and for ``read``. Everything kept verbatim."""
        lines: list[str] = []
        if self.caption:
            lines.append(self.caption)
        lines.extend(self.header_lines)
        lines.extend(" | ".join(row) for row in self.rows)
        return "\n".join(lines).strip()


def _clean_cell(value: str | None) -> str:
    """Make a cell printable without altering what it says.

    pdfplumber inserts newlines where a cell wraps across lines; those become
    spaces. Nothing else changes — no case folding, no unit rewriting, no
    stripping of symbols like ``°`` or ``µ``.
    """
    if value is None:
        return ""
    return re.sub(r"\s*\n\s*", " ", value).strip()


def is_table_caption(text: str) -> bool:
    return bool(_TABLE_CAPTION.match(text))


def find_caption(page: PageText, table_bbox: BBox, window_pt: float) -> str | None:
    """Nearest ``Table N ...`` caption above the table, else below it.

    Government reports put the caption above the rule; a few put it below. Both
    are checked, nearest first, within ``window_pt`` points. Continuation lines
    are joined on, because these captions wrap.
    """
    top, bottom = table_bbox[1], table_bbox[3]
    above: list[tuple[float, Line]] = []
    below: list[tuple[float, Line]] = []

    for block in page.blocks:
        for line in block.lines:
            if not _TABLE_CAPTION.match(line.text):
                continue
            if line.bottom <= top and (top - line.bottom) <= window_pt:
                above.append((top - line.bottom, line))
            elif line.top >= bottom and (line.top - bottom) <= window_pt:
                below.append((line.top - bottom, line))

    for candidates in (above, below):
        if candidates:
            first = min(candidates, key=lambda item: item[0])[1]
            return extend_caption(page, first, is_table_caption)
    return None


def numeric_line_count(page: PageText, token_frac: float) -> int:
    """Lines that read as a row of figures rather than a sentence.

    This is the second candidate signal, and the only one that finds the NACA
    report's tables: they are captionless, so nothing else marks them out.
    """
    count = 0
    for block in page.blocks:
        for line in block.lines:
            tokens = line.text.split()
            if len(tokens) < 3:
                continue
            numeric = sum(1 for token in tokens if _NUMERIC_TOKEN.match(token))
            if numeric / len(tokens) >= token_frac:
                count += 1
    return count


def is_table_candidate(page: PageText, config: Config) -> bool:
    """Whether a page is worth handing to pdfplumber.

    Deciding this from PyMuPDF's output first is what keeps the build tractable:
    pdfplumber has to materialise every vector object on a page, and the map
    pages in these reports take 15-70 seconds each to load. Pages carrying a
    table caption or a block of numeric rows are the ones that can hold a table;
    the rest are skipped, and the ledger records that the rule did it.
    """
    if not config.table_require_candidate:
        return True
    if any(_TABLE_CAPTION.match(line.text) for block in page.blocks for line in block.lines):
        return True
    return numeric_line_count(page, config.table_numeric_token_frac) >= config.table_numeric_line_min


def _is_row_like(text: str, token_frac: float) -> bool:
    tokens = text.split()
    if len(tokens) < 3:
        return False
    numeric = sum(1 for token in tokens if _NUMERIC_TOKEN.match(token))
    return numeric / len(tokens) >= token_frac


def table_bands(page: PageText, config: Config) -> list[TableBand]:
    """Vertical bands of the page that hold a table, excluding its caption.

    This is the step that makes column detection usable. pdfplumber's ``text``
    strategy derives one set of column boundaries for everything it is given, so
    handing it a whole page slices the full-width caption and footnotes along the
    data columns' edges — the caption comes back as ``['Table 1. Infor',
    'mation abo', 'ut chemical compounds', ...]``. Cropping to the band first
    means the boundaries are derived from the grid alone.

    A band runs from the first header line to the last data row:

    * the **data rows** are a contiguous run of lines that read as rows of
      figures, which is what distinguishes them from prose;
    * the **header lines** are the few lines directly above that run — they are
      prose-like, and they are where the units live, so they must be inside the
      band;
    * the **caption** is deliberately left outside and attached separately by
      :func:`find_caption`, keeping its wording intact.
    """
    lines = sorted(
        (line for block in page.blocks for line in block.lines), key=lambda line: line.top
    )
    if not lines:
        return []

    leadings = [
        b.top - a.bottom for a, b in zip(lines, lines[1:]) if 0 <= (b.top - a.bottom) < 40
    ]
    leading = median(leadings) if leadings else 4.0
    max_gap = max(leading * 3.0, 6.0)

    row_like = [_is_row_like(line.text, config.table_numeric_token_frac) for line in lines]

    bands: list[TableBand] = []
    index = 0
    while index < len(lines):
        if not row_like[index]:
            index += 1
            continue

        start = index
        end = index
        while end + 1 < len(lines) and (
            row_like[end + 1] or (lines[end + 1].top - lines[end].bottom) <= leading * 1.2
        ):
            # Stop extending once we leave the grid: a wide vertical gap means the
            # table ended and prose resumed.
            if (lines[end + 1].top - lines[end].bottom) > max_gap:
                break
            end += 1

        run = lines[start : end + 1]
        if sum(row_like[start : end + 1]) < config.table_min_rows:
            index = end + 1
            continue

        # Walk back over the header lines sitting immediately above the run,
        # stopping at the caption or at another table's data — a header must never
        # swallow the tail of the table above it.
        header_start = start
        while (
            header_start > 0
            and (start - header_start) < config.table_header_lines_max
            and (lines[header_start].top - lines[header_start - 1].bottom) <= max_gap
            and not _TABLE_CAPTION.match(lines[header_start - 1].text)
            and not row_like[header_start - 1]
        ):
            header_start -= 1

        header = lines[header_start:start]
        members = lines[header_start : end + 1]
        bands.append(
            TableBand(
                bbox=(
                    min(line.bbox[0] for line in members) - 2.0,
                    min(line.bbox[1] for line in members) - 2.0,
                    max(line.bbox[2] for line in members) + 2.0,
                    max(line.bbox[3] for line in run) + 2.0,
                ),
                data_bbox=(
                    min(line.bbox[0] for line in run) - 2.0,
                    min(line.bbox[1] for line in run) - 2.0,
                    max(line.bbox[2] for line in run) + 2.0,
                    max(line.bbox[3] for line in run) + 2.0,
                ),
                header_lines=tuple(line.text for line in header),
            )
        )
        index = end + 1

    return bands


def _table_settings(strategy: str) -> dict[str, str]:
    if strategy == "lines":
        return {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
    return {"vertical_strategy": "text", "horizontal_strategy": "text"}


def looks_like_table(rows: list[list[str]], config: Config) -> bool:
    """Reject candidate grids that are really aligned prose.

    A table has columns that most rows actually populate, and short cells. A
    paragraph picked up by whitespace alignment fails one or both: its columns
    are filled sporadically, or a cell runs to hundreds of characters.
    """
    if len(rows) < config.table_min_rows:
        return False
    n_cols = max(len(row) for row in rows)
    if n_cols < config.table_min_cols:
        return False

    filled_per_row = [sum(1 for cell in row if cell) for row in rows]
    multi_column = sum(1 for n in filled_per_row if n >= config.table_min_cols)
    if multi_column / len(rows) < config.table_min_filled_row_frac:
        return False

    # Column consistency: a real table has at least `table_min_cols` columns that
    # most rows fill. Shredded prose spreads words over many sparse columns.
    consistent = 0
    for col in range(n_cols):
        filled = sum(1 for row in rows if col < len(row) and row[col])
        if filled / len(rows) >= config.table_min_col_fill:
            consistent += 1
    if consistent < config.table_min_cols:
        return False

    cells = [cell for row in rows for cell in row if cell]
    if not cells:
        return False
    if sum(len(cell) for cell in cells) / len(cells) > config.table_max_mean_cell_chars:
        return False
    if plausible_cell_fraction(rows) < config.table_min_plausible_cell_frac:
        return False
    return True


def is_labelled(caption: str | None, header_lines: tuple[str, ...] | list[str]) -> bool:
    """Whether a band is a published table rather than a run of loose figures.

    A real table is titled, or its columns are named, or both — that is how a
    reader knows what the numbers mean, and it is where the units are written.
    A band with neither a caption nor a header carrying actual words is numeric
    debris: on the 1909 scan these come back as ``['6,653 6,161 2,71 8,61
    8,5', ...]``, which passes every statistical test for a table and means
    nothing. Requiring a label is what separates the two.
    """
    if caption:
        return True
    words = sum(
        len(re.findall(r"[^\W\d_]{3,}", line, re.UNICODE)) for line in header_lines
    )
    return words >= 2


def cell_is_plausible(cell: str) -> bool:
    """Whether a cell reads as a real value rather than OCR debris.

    Two shapes count: a run of figures (``67.3``, or the multi-value cells the
    NACA tables use), and anything containing a word of four or more letters.
    What fails is the wreckage a bad OCR layer produces — ``'esr'``, ``'rewo'``,
    ``'.sl'`` — where the scan's vertical or rotated text came back reversed and
    chopped.
    """
    tokens = cell.split()
    if not tokens:
        return False
    numeric = sum(1 for token in tokens if _NUMERIC_TOKEN.match(token))
    if numeric / len(tokens) >= 0.5:
        return True
    return bool(re.search(r"[^\W\d_]{4,}", cell, re.UNICODE))


def plausible_cell_fraction(rows: list[list[str]]) -> float:
    """Fraction of non-empty cells that read as real values."""
    cells = [cell for row in rows for cell in row if cell]
    if not cells:
        return 0.0
    return sum(1 for cell in cells if cell_is_plausible(cell)) / len(cells)


def _rows_in_band(
    plumber_page: pdfplumber.page.Page, band: BBox, strategy: str
) -> list[list[str]]:
    """Run pdfplumber inside one band and return its cleaned rows."""
    clipped = (
        max(band[0], float(plumber_page.bbox[0])),
        max(band[1], float(plumber_page.bbox[1])),
        min(band[2], float(plumber_page.bbox[2])),
        min(band[3], float(plumber_page.bbox[3])),
    )
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return []

    region = plumber_page.crop(clipped)
    tables = region.find_tables(table_settings=_table_settings(strategy))
    if not tables:
        return []

    best = max(tables, key=lambda t: len(t.rows))
    rows = [[_clean_cell(cell) for cell in row] for row in best.extract() or []]
    return [row for row in rows if any(cell for cell in row)]


def extract_page_tables(
    plumber_page: pdfplumber.page.Page,
    page_text: PageText,
    config: Config,
) -> tuple[list[ExtractedTable], int]:
    """Extract every table on one page, one band at a time.

    Both detection strategies are tried per band and the fuller result wins: on
    a region already known to be a grid, ``text`` usually recovers every data
    row where ``lines`` recovers only the handful that happen to be ruled.

    Returns the accepted tables and the number of bands rejected by the quality
    filter, so the caller can say in the ledger that a table was seen on the page
    but could not be read.
    """
    found: list[ExtractedTable] = []
    rejected = 0
    alternate = "lines" if config.table_strategy == "text" else "text"

    for band in table_bands(page_text, config):
        caption = find_caption(page_text, band.bbox, config.caption_window_pt)
        if config.table_require_label and not is_labelled(caption, band.header_lines):
            rejected += 1
            continue

        candidates = [
            _rows_in_band(plumber_page, band.data_bbox, config.table_strategy),
            _rows_in_band(plumber_page, band.data_bbox, alternate),
        ]
        valid = [rows for rows in candidates if looks_like_table(rows, config)]
        if not valid:
            if any(candidates):
                rejected += 1
            continue

        found.append(
            ExtractedTable(
                page_number=page_text.page_number,
                bbox=band.bbox,
                header_lines=list(band.header_lines),
                rows=max(valid, key=len),
                caption=caption,
            )
        )

    return found, rejected
