"""Queries behind ``search``, ``read``, ``context`` and ``visual``.

Ranking uses FTS5's BM25 with headings and captions weighted above body text: a
query that matches a section heading or a figure caption is usually a better hit
than the same words buried in a paragraph.

Every result carries its own provenance — source path, publisher, date, page —
because a snippet without them cannot be checked.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

#: BM25 column weights, in the FTS table's column order.
_WEIGHTS = (1.0, 2.5, 2.0, 1.5)  # text, heading, caption, image_description

_TOKEN = re.compile(r'"[^"]*"|\S+')


@dataclass(frozen=True)
class Hit:
    """One ranked search result."""

    unit_id: str
    source_path: str
    publisher: str | None
    doc_date: str | None
    page: int | None
    heading: str | None
    unit_type: str
    snippet: str
    rank: float


def to_match_query(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH expression.

    Bare user input is not valid FTS5 syntax — ``mg/L`` and ``(°C)`` both
    contain characters the parser treats as operators. Each whitespace-separated
    term is quoted into a phrase and the terms are ANDed, so queries read
    literally and no input can produce a syntax error. Double-quoted phrases in
    the input are preserved.
    """
    terms: list[str] = []
    for raw in _TOKEN.findall(query):
        term = raw.strip('"').replace('"', "")
        if term:
            terms.append(f'"{term}"')
    return " AND ".join(terms)


def search(
    conn: sqlite3.Connection, query: str, limit: int = 10, unit_type: str | None = None
) -> list[Hit]:
    """Ranked full-text search over passages, tables, captions and descriptions."""
    match = to_match_query(query)
    if not match:
        return []

    sql = """
        SELECT u.unit_id, u.source_path, u.publisher, u.doc_date, u.page,
               u.heading, u.unit_type,
               snippet(units_fts, 0, '[', ']', ' … ', 24) AS snippet,
               bm25(units_fts, ?, ?, ?, ?) AS rank
          FROM units_fts
          JOIN units AS u ON u.rowid = units_fts.rowid
         WHERE units_fts MATCH ?
    """
    params: list[object] = [*_WEIGHTS, match]
    if unit_type:
        sql += " AND u.unit_type = ?"
        params.append(unit_type)
    # bm25 returns negative scores; more negative is a better match.
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    return [
        Hit(
            unit_id=row["unit_id"],
            source_path=row["source_path"],
            publisher=row["publisher"],
            doc_date=row["doc_date"],
            page=row["page"],
            heading=row["heading"],
            unit_type=row["unit_type"],
            snippet=(row["snippet"] or "").replace("\n", " ").strip(),
            rank=row["rank"],
        )
        for row in conn.execute(sql, params).fetchall()
    ]


def get_unit(conn: sqlite3.Connection, unit_id: str) -> sqlite3.Row | None:
    """One unit with its file's full provenance."""
    return conn.execute(
        """SELECT u.*, f.sha256, f.source_url, f.license, f.title, f.media_type,
                  f.parent_archive_id, p.logical_path AS parent_path
             FROM units AS u
             JOIN files AS f ON f.file_id = u.file_id
        LEFT JOIN files AS p ON p.file_id = f.parent_archive_id
            WHERE u.unit_id = ?""",
        (unit_id,),
    ).fetchone()


def neighbours(conn: sqlite3.Connection, unit: sqlite3.Row) -> list[sqlite3.Row]:
    """Units on the same page of the same file, in reading order.

    Scoped to one file by construction: context never crosses a source boundary.
    """
    if unit["page"] is None:
        return conn.execute(
            """SELECT * FROM units
                WHERE file_id = ? AND page IS NULL
             ORDER BY page_ordinal""",
            (unit["file_id"],),
        ).fetchall()
    return conn.execute(
        """SELECT * FROM units
            WHERE file_id = ? AND page = ?
         ORDER BY page_ordinal""",
        (unit["file_id"], unit["page"]),
    ).fetchall()
