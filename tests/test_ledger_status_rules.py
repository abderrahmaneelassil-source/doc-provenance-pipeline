"""The ledger's status rules.

The project's central claim is that a step which ran is not the same as content
that was recovered. These tests hold that line: the status set is closed, zero
yield can never be written as ``recovered``, and each non-success status carries
the information that makes it actionable.
"""

from __future__ import annotations

import sqlite3

import pytest

from dpp import ledger
from dpp.db import STATUSES


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM ledger ORDER BY ledger_id").fetchall()


def test_the_status_set_is_exactly_the_five(conn: sqlite3.Connection, register_file):
    assert set(STATUSES) == {
        "recovered",
        "no_new_content",
        "failed",
        "source_dependent",
        "skipped_by_rule",
    }


def test_inventing_a_status_is_refused(conn: sqlite3.Connection, register_file):
    file_id = register_file()
    with pytest.raises(ledger.InvalidStatus):
        ledger.record(conn, file_id, ledger.STEP_OCR, "partially_recovered", "...")


def test_the_database_also_refuses_an_unknown_status(
    conn: sqlite3.Connection, register_file
):
    """A CHECK constraint backs the Python guard, so no path can bypass it."""
    file_id = register_file()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ledger (file_id, step, status, detail, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, "ocr", "mostly_fine", "", "2026-01-01T00:00:00+00:00"),
        )


def test_zero_recovery_is_no_new_content_not_recovered(
    conn: sqlite3.Connection, register_file
):
    file_id = register_file()
    status = ledger.record_outcome(
        conn, file_id, ledger.STEP_OCR, recovered_count=0, detail="ocr_chars=11 gain=11 words=2"
    )

    assert status == "no_new_content"
    assert _rows(conn)[0]["status"] == "no_new_content"


def test_positive_recovery_is_recovered(conn: sqlite3.Connection, register_file):
    file_id = register_file()
    status = ledger.record_outcome(
        conn, file_id, ledger.STEP_OCR, recovered_count=3, detail="3 pages"
    )

    assert status == "recovered"
    assert _rows(conn)[0]["status"] == "recovered"


def test_a_step_cannot_claim_recovery_without_a_count(
    conn: sqlite3.Connection, register_file
):
    """``record_outcome`` derives the status, so callers cannot assert success."""
    file_id = register_file()
    for count in (0, -1):
        ledger.record_outcome(conn, file_id, ledger.STEP_TABLES, recovered_count=count)

    assert [row["status"] for row in _rows(conn)] == ["no_new_content", "no_new_content"]


def test_failures_keep_the_exception_type_and_message(
    conn: sqlite3.Connection, register_file
):
    file_id = register_file()
    ledger.record_failure(
        conn, file_id, ledger.STEP_TABLES, ValueError("negative page height"), "page 7:"
    )

    row = _rows(conn)[0]
    assert row["status"] == "failed"
    assert "ValueError" in row["detail"]
    assert "negative page height" in row["detail"]
    assert row["detail"].startswith("page 7:")


def test_source_dependent_says_what_is_needed(conn: sqlite3.Connection, register_file):
    file_id = register_file()
    ledger.record_source_dependent(
        conn, file_id, ledger.STEP_OFFICE, "legacy .doc requires LibreOffice"
    )

    row = _rows(conn)[0]
    assert row["status"] == "source_dependent"
    assert "needs export or replacement" in row["detail"]
    assert "LibreOffice" in row["detail"]


def test_skipped_by_rule_names_the_rule(conn: sqlite3.Connection, register_file):
    file_id = register_file()
    ledger.record_skipped(
        conn, file_id, ledger.STEP_IMAGES, "image_min_px: 14x980px below image_min_px=80"
    )

    row = _rows(conn)[0]
    assert row["status"] == "skipped_by_rule"
    assert "image_min_px=80" in row["detail"]


def test_every_row_is_timestamped_and_tied_to_a_file(
    conn: sqlite3.Connection, register_file
):
    file_id = register_file()
    ledger.record(conn, file_id, ledger.STEP_TEXT, "recovered", "24 pages")

    row = _rows(conn)[0]
    assert row["file_id"] == file_id
    assert row["timestamp"].startswith("20")
    assert row["step"] == ledger.STEP_TEXT


def test_counts_include_statuses_that_never_occurred(
    conn: sqlite3.Connection, register_file
):
    file_id = register_file()
    ledger.record(conn, file_id, ledger.STEP_TEXT, "recovered", "")

    counts = ledger.status_counts(conn)
    assert counts["recovered"] == 1
    assert counts["failed"] == 0
    assert set(counts) == set(STATUSES), "a zero must be reported, not omitted"
