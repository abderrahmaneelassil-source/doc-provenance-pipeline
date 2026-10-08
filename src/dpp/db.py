"""SQLite schema and connection handling.

Three tables carry the whole model:

``files``   one row per file, including archive members (which point back at
            their parent through ``parent_archive_id``).
``ledger``  one row per file per processing step, with an honest status.
``units``   the searchable, readable pieces. Each one keeps the identity of the
            single file it came from — units are never built from two sources.

``units_fts`` is an FTS5 index over the units' searchable columns. It uses
external-content mode so the text is stored once, in ``units``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: The only statuses a ledger row may carry. A step that ran but recovered
#: nothing is ``no_new_content`` — never ``recovered``.
STATUSES = (
    "recovered",
    "no_new_content",
    "failed",
    "source_dependent",
    "skipped_by_rule",
)

UNIT_TYPES = ("passage", "table", "figure")

SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    file_id           TEXT PRIMARY KEY,
    logical_path      TEXT NOT NULL UNIQUE,
    stored_path       TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    media_type        TEXT NOT NULL,
    parent_archive_id TEXT REFERENCES files(file_id),
    title             TEXT,
    publisher         TEXT,
    doc_date          TEXT,
    source_url        TEXT,
    license           TEXT,
    first_seen        TEXT NOT NULL,
    last_processed    TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id   TEXT NOT NULL REFERENCES files(file_id),
    step      TEXT NOT NULL,
    status    TEXT NOT NULL CHECK (status IN ({", ".join(repr(s) for s in STATUSES)})),
    detail    TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ledger_file_idx   ON ledger(file_id);
CREATE INDEX IF NOT EXISTS ledger_status_idx ON ledger(status);

CREATE TABLE IF NOT EXISTS units (
    unit_id      TEXT PRIMARY KEY,
    file_id      TEXT NOT NULL REFERENCES files(file_id),
    source_path  TEXT NOT NULL,
    publisher    TEXT,
    doc_date     TEXT,
    page         INTEGER,
    heading      TEXT,
    unit_type    TEXT NOT NULL CHECK (unit_type IN ({", ".join(repr(t) for t in UNIT_TYPES)})),
    text         TEXT NOT NULL,
    caption      TEXT,
    image_path   TEXT,
    image_description TEXT,
    bbox         TEXT,
    table_rows   TEXT,
    page_ordinal INTEGER NOT NULL,
    from_ocr     INTEGER NOT NULL DEFAULT 0,
    -- Mean Tesseract word confidence for the page this unit came from, when it
    -- came from OCR. Kept per unit because no cheap metric distinguishes
    -- recovered-and-accurate from recovered-and-garbled, so the reader needs the
    -- number next to the text. See the OCR limitation in the README.
    ocr_confidence REAL
);

CREATE INDEX IF NOT EXISTS units_file_idx ON units(file_id);
CREATE INDEX IF NOT EXISTS units_page_idx ON units(file_id, page, page_ordinal);

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    text, heading, caption, image_description,
    content='units', content_rowid='rowid', tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS units_fts_ai AFTER INSERT ON units BEGIN
    INSERT INTO units_fts(rowid, text, heading, caption, image_description)
    VALUES (new.rowid, new.text, new.heading, new.caption, new.image_description);
END;

CREATE TRIGGER IF NOT EXISTS units_fts_ad AFTER DELETE ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, text, heading, caption, image_description)
    VALUES ('delete', old.rowid, old.text, old.heading, old.caption, old.image_description);
END;

CREATE TRIGGER IF NOT EXISTS units_fts_au AFTER UPDATE ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, text, heading, caption, image_description)
    VALUES ('delete', old.rowid, old.text, old.heading, old.caption, old.image_description);
    INSERT INTO units_fts(rowid, text, heading, caption, image_description)
    VALUES (new.rowid, new.text, new.heading, new.caption, new.image_description);
END;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the pipeline database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def fts5_available() -> bool:
    """Whether this interpreter's SQLite was built with FTS5."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()
