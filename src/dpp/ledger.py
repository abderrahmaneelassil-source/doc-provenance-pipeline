"""The ledger: one row per file per processing step, with an honest status.

The whole point of this module is the distinction the five statuses encode:

``recovered``        the step ran and produced substantive new content
``no_new_content``   the step ran, completed, and produced nothing substantive
``failed``           the step raised; the exception message is the detail
``source_dependent`` the source cannot be processed as supplied and needs an
                     export or a replacement from whoever published it
``skipped_by_rule``  a configured rule excluded the item, and the detail names
                     the rule and its threshold

``record_outcome`` exists so callers cannot accidentally write ``recovered`` for
an empty result: they pass the measured amount of recovered content and the
status is derived from it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import STATUSES

# Step names used across the pipeline. Kept as constants so the report can group
# by step without string literals drifting between modules.
STEP_INVENTORY = "inventory"
STEP_ARCHIVE = "archive_expansion"
STEP_TEXT = "text_extraction"
STEP_OCR = "ocr"
STEP_HEADINGS = "headings"
STEP_TABLES = "tables"
STEP_IMAGES = "images"
STEP_DESCRIBE = "image_description"
STEP_OFFICE = "office_conversion"
STEP_INDEX = "indexing"


class InvalidStatus(ValueError):
    """Raised when a caller invents a status outside the allowed five."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    conn: sqlite3.Connection,
    file_id: str,
    step: str,
    status: str,
    detail: str = "",
) -> None:
    """Write one ledger row. Rejects statuses outside the allowed set."""
    if status not in STATUSES:
        raise InvalidStatus(f"{status!r} is not one of {STATUSES}")
    conn.execute(
        "INSERT INTO ledger (file_id, step, status, detail, timestamp) VALUES (?, ?, ?, ?, ?)",
        (file_id, step, status, detail, _now()),
    )


def record_outcome(
    conn: sqlite3.Connection,
    file_id: str,
    step: str,
    *,
    recovered_count: int,
    detail: str = "",
) -> str:
    """Record a step that ran to completion, deriving the status from its yield.

    ``recovered_count`` is however the step counts substantive output: units
    produced, pages with text, tables found. Zero means the step ran and
    recovered nothing, which is ``no_new_content`` — not ``recovered``.
    """
    status = "recovered" if recovered_count > 0 else "no_new_content"
    record(conn, file_id, step, status, detail)
    return status


def record_failure(
    conn: sqlite3.Connection,
    file_id: str,
    step: str,
    error: BaseException,
    note: str = "",
) -> None:
    """Record a step that raised, keeping the exception type and message."""
    message = f"{type(error).__name__}: {error}"
    record(conn, file_id, step, "failed", f"{note} {message}".strip())


def record_source_dependent(
    conn: sqlite3.Connection, file_id: str, step: str, reason: str
) -> None:
    """Record a source that cannot be processed as supplied."""
    record(
        conn,
        file_id,
        step,
        "source_dependent",
        f"needs export or replacement: {reason}",
    )


def record_skipped(
    conn: sqlite3.Connection, file_id: str, step: str, rule: str
) -> None:
    """Record items excluded by a configured rule. ``rule`` must name the rule."""
    record(conn, file_id, step, "skipped_by_rule", rule)


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Ledger row counts by status, including the statuses that never occurred."""
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM ledger GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    counts.update({row["status"]: row["n"] for row in rows})
    return counts
