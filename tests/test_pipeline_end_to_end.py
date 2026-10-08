"""End to end: build, then refresh, over a tiny corpus built in the test.

This is the test that would catch a stage wired up wrongly — ledger rows written
against the wrong file, units losing their metadata, or ``refresh`` reprocessing
everything. It needs no network, no API key, and no Tesseract.
"""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from conftest import make_pdf

from dpp import db, ledger, pipeline, report, search
from dpp.config import Config

SOURCES = """# Sources

## report.pdf
- title: Potential corrosivity of untreated groundwater
- publisher: U.S. Geological Survey
- date: 2016-07-12
- source_url: https://pubs.usgs.gov/sir/2016/5092/sir20165092.pdf
- license: Public domain

## bundle.zip
- publisher: U.S. Geological Survey
- date: unknown

## bundle.zip!/factsheet.pdf
- publisher: U.S. Geological Survey
- date: 2013
"""

BODY = (
    "Dissolved solids concentrations ranged from 112 to 884 mg/L across the "
    "sampled wells during the study period, and field water temperature ranged "
    "from 9.4 to 21.7 degrees Celsius at the same sites."
)


def _corpus(tmp_path: Path) -> Path:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "SOURCES.md").write_text(SOURCES, encoding="utf-8")

    make_pdf(samples / "report.pdf", [BODY, BODY + " Second page of the same report."])

    inner = tmp_path / "factsheet.pdf"
    make_pdf(
        inner,
        [
            "The 3D Elevation Program collects lidar data for Alabama. Quality "
            "level 2 lidar covers the whole state, and the derived products "
            "include bare-earth digital elevation models at one metre."
        ],
    )
    with zipfile.ZipFile(samples / "bundle.zip", "w") as zf:
        zf.write(inner, arcname="factsheet.pdf")

    return samples


def test_build_then_refresh(tmp_path: Path, config: Config):
    samples = _corpus(tmp_path)
    conn = db.connect(config.db_path)

    # --- build ---------------------------------------------------------
    first = pipeline.run(samples, config, conn)
    assert first.files_seen == 3, "report.pdf, bundle.zip, and the archive member"
    assert first.files_processed == 3
    assert first.units_written > 0
    assert not first.notes, f"no file should fail: {first.notes}"

    # --- metadata reached the units -----------------------------------
    hits = search.search(conn, "mg/L")
    assert hits
    assert hits[0].publisher == "U.S. Geological Survey"
    assert hits[0].doc_date == "2016-07-12"
    assert hits[0].page in (1, 2)

    # The sidecar said `unknown` for the archive's date; that is stored as
    # absent rather than guessed.
    archive_date = conn.execute(
        "SELECT doc_date FROM files WHERE logical_path = 'bundle.zip'"
    ).fetchone()["doc_date"]
    assert archive_date is None

    # --- the archive member is linked to its parent --------------------
    member = conn.execute(
        "SELECT * FROM files WHERE logical_path = 'bundle.zip!/factsheet.pdf'"
    ).fetchone()
    parent = conn.execute(
        "SELECT file_id FROM files WHERE logical_path = 'bundle.zip'"
    ).fetchone()
    assert member["parent_archive_id"] == parent["file_id"]
    assert search.search(conn, "lidar"), "archive members are searchable"

    # --- the ledger recorded every file at every applicable step -------
    counts = ledger.status_counts(conn)
    assert counts["recovered"] > 0
    assert counts["failed"] == 0
    steps = {row["step"] for row in report.step_matrix(conn)}
    assert {ledger.STEP_INVENTORY, ledger.STEP_TEXT, ledger.STEP_INDEX} <= steps

    # Pages of plain prose are not sparse, so OCR must have been a no-op.
    ocr_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM ledger WHERE step = ?", (ledger.STEP_OCR,)
    ).fetchone()["n"]
    assert ocr_rows == 0, "OCR must not run on pages that already have text"

    # --- refresh with nothing changed ----------------------------------
    second = pipeline.run(samples, config, conn, only_changed=True)
    assert second.files_unchanged == 3
    assert second.files_processed == 0
    assert second.units_written == 0

    units_after = conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]

    # --- refresh after editing one file --------------------------------
    make_pdf(samples / "report.pdf", [BODY + " Revised with new findings."])
    third = pipeline.run(samples, config, conn, only_changed=True)
    assert third.files_processed == 1, "only the edited file is reprocessed"
    assert third.files_unchanged == 2
    assert search.search(conn, "Revised")

    # The rewritten file shrank from two pages to one; its old units are gone.
    assert conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"] != units_after
    assert not search.search(conn, "Second page of the same report")

    conn.close()


def test_a_file_with_no_metadata_entry_is_recorded_as_unknown(
    tmp_path: Path, config: Config
):
    samples = tmp_path / "samples"
    samples.mkdir()
    make_pdf(samples / "orphan.pdf", [BODY])
    conn = db.connect(config.db_path)

    pipeline.run(samples, config, conn)
    row = conn.execute("SELECT publisher, doc_date FROM files").fetchone()

    assert row["publisher"] is None, "nothing is invented for a file with no entry"
    assert row["doc_date"] is None
    conn.close()


def test_an_unreadable_pdf_is_recorded_as_failed_and_the_run_continues(
    tmp_path: Path, config: Config
):
    samples = tmp_path / "samples"
    samples.mkdir()
    make_pdf(samples / "good.pdf", [BODY])
    (samples / "broken.pdf").write_bytes(b"%PDF-1.7\nthis is not a pdf body")
    conn = db.connect(config.db_path)

    result = pipeline.run(samples, config, conn)

    assert result.files_processed == 2
    failed = report.rows_with_status(conn, "failed")
    assert [row["logical_path"] for row in failed] == ["broken.pdf"]
    assert search.search(conn, "mg/L"), "the healthy file was still indexed"
    conn.close()


def test_report_counts_match_the_ledger(tmp_path: Path, config: Config):
    samples = _corpus(tmp_path)
    conn = db.connect(config.db_path)
    pipeline.run(samples, config, conn)

    counts = report.status_counts(conn)
    total = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]

    assert sum(counts.values()) == total
    summary = report.corpus_summary(conn)
    assert summary["files"] == 3
    assert summary["units"] > 0
    conn.close()


def test_describe_is_off_by_default_and_writes_no_descriptions(
    tmp_path: Path, config: Config
):
    """The zero-cost guarantee: no flag, no API client, no fabricated text."""
    samples = _corpus(tmp_path)
    conn = db.connect(config.db_path)

    assert config.describe_images is False
    assert pipeline.make_describer(config) is None

    pipeline.run(samples, config, conn, describer=None)
    described = conn.execute(
        "SELECT COUNT(*) AS n FROM units WHERE image_description IS NOT NULL"
    ).fetchone()["n"]
    assert described == 0

    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM ledger WHERE step = ?", (ledger.STEP_DESCRIBE,)
    ).fetchone()["n"]
    assert rows == 0, "the description step must not even be recorded when off"
    conn.close()
