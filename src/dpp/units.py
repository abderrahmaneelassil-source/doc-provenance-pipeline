"""The searchable unit: one piece of one document, with its provenance.

The single invariant: **a unit comes from exactly one file.** Nothing in this
module merges text across sources, and the identity of the source travels with
the unit rather than being looked up later — so a search result can always say
where it came from without a join that might go missing.

Unit text is stored verbatim. No paraphrasing, no normalising, no unit
conversion: ``mg/L``, ``°C`` and ``kt`` reach the index exactly as the publisher
wrote them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

PASSAGE = "passage"
TABLE = "table"
FIGURE = "figure"


@dataclass
class Unit:
    """One searchable, readable piece of a document."""

    file_id: str
    source_path: str
    unit_type: str
    text: str
    page: int | None
    """1-based page number, or ``None`` for formats with no fixed pagination."""

    page_ordinal: int
    """Reading-order position within the page, used by ``context``."""

    publisher: str | None = None
    doc_date: str | None = None
    heading: str | None = None
    caption: str | None = None
    image_path: str | None = None
    image_description: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    table_rows: str | None = None
    from_ocr: bool = False
    ocr_confidence: float | None = None
    """Mean Tesseract word confidence for the page, when the text came from OCR.
    Stored per unit because ``recovered`` means text was recovered, not that it
    is accurate — the reader needs the number beside the text."""

    unit_id: str = field(default="")

    def assign_id(self) -> str:
        """Build a readable, stable id: ``<file><page><ordinal>``.

        Readable matters because users type these into ``read``, ``context`` and
        ``visual``. Stable matters because ``refresh`` rebuilds a file's units
        from scratch, and an unchanged file must produce the same ids.
        """
        page = 0 if self.page is None else self.page
        self.unit_id = f"{self.file_id[:8]}-p{page:04d}-{self.page_ordinal:03d}"
        return self.unit_id


def insert(conn: sqlite3.Connection, unit: Unit) -> str:
    """Persist one unit. The FTS index is maintained by trigger."""
    if not unit.unit_id:
        unit.assign_id()
    conn.execute(
        """INSERT INTO units (unit_id, file_id, source_path, publisher, doc_date, page,
                              heading, unit_type, text, caption, image_path,
                              image_description, bbox, table_rows, page_ordinal, from_ocr,
                              ocr_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            unit.unit_id,
            unit.file_id,
            unit.source_path,
            unit.publisher,
            unit.doc_date,
            unit.page,
            unit.heading,
            unit.unit_type,
            unit.text,
            unit.caption,
            str(unit.image_path) if unit.image_path else None,
            unit.image_description,
            json.dumps(list(unit.bbox)) if unit.bbox else None,
            unit.table_rows,
            unit.page_ordinal,
            int(unit.from_ocr),
            unit.ocr_confidence,
        ),
    )
    return unit.unit_id


def delete_for_file(conn: sqlite3.Connection, file_id: str) -> int:
    """Drop every unit of one file, so it can be reprocessed cleanly."""
    cursor = conn.execute("DELETE FROM units WHERE file_id = ?", (file_id,))
    return cursor.rowcount
