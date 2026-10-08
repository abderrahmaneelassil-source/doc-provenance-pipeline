"""The ``report`` command: counts by status, plus what did not work.

The failures and source-dependent items are listed in full rather than counted,
because they are the actionable part: a ``failed`` row needs a fix here, and a
``source_dependent`` row needs something from whoever published the file.
"""

from __future__ import annotations

import sqlite3

from .db import STATUSES


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM ledger GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    counts.update({row["status"]: row["n"] for row in rows})
    return counts


def step_matrix(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Counts by (step, status), so it is clear which stage recovered what."""
    return conn.execute(
        """SELECT step, status, COUNT(*) AS n
             FROM ledger
         GROUP BY step, status
         ORDER BY step, status"""
    ).fetchall()


def rows_with_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.logical_path, l.step, l.status, l.detail, l.timestamp
             FROM ledger AS l
             JOIN files AS f ON f.file_id = l.file_id
            WHERE l.status = ?
         ORDER BY f.logical_path, l.step""",
        (status,),
    ).fetchall()


def corpus_summary(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        """SELECT (SELECT COUNT(*) FROM files)  AS files,
                  (SELECT COUNT(*) FROM files WHERE parent_archive_id IS NOT NULL)
                                                AS archive_members,
                  (SELECT COUNT(*) FROM units)  AS units,
                  (SELECT COUNT(*) FROM units WHERE unit_type = 'passage') AS passages,
                  (SELECT COUNT(*) FROM units WHERE unit_type = 'table')   AS tables,
                  (SELECT COUNT(*) FROM units WHERE unit_type = 'figure')  AS figures,
                  (SELECT COUNT(*) FROM units WHERE from_ocr = 1)          AS ocr_units,
                  (SELECT COUNT(*) FROM units WHERE image_description IS NOT NULL)
                                                AS described_figures,
                  (SELECT COUNT(DISTINCT publisher) FROM files WHERE publisher IS NOT NULL)
                                                AS publishers"""
    ).fetchone()


def per_file(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.logical_path, f.media_type, f.size_bytes, f.publisher, f.doc_date,
                  COUNT(u.unit_id) AS units,
                  SUM(CASE WHEN u.unit_type = 'table'  THEN 1 ELSE 0 END) AS tables,
                  SUM(CASE WHEN u.unit_type = 'figure' THEN 1 ELSE 0 END) AS figures,
                  SUM(CASE WHEN u.from_ocr = 1 THEN 1 ELSE 0 END)         AS ocr_units
             FROM files AS f
        LEFT JOIN units AS u ON u.file_id = f.file_id
         GROUP BY f.file_id
         ORDER BY f.logical_path"""
    ).fetchall()
