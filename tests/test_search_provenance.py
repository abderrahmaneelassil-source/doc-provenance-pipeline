"""Search must return page numbers, and the rest of each unit's provenance.

A snippet nobody can locate in the source is not a usable result, so the page
number is asserted directly. The sibling concern is also tested here: units are
never built from two sources, and ``context`` cannot wander across files.
"""

from __future__ import annotations

import sqlite3

import pytest

from dpp import search as search_mod
from dpp import units


def _unit(file_id: str, path: str, page: int | None, ordinal: int, text: str, **kw) -> units.Unit:
    unit = units.Unit(
        file_id=file_id,
        source_path=path,
        unit_type=kw.pop("unit_type", units.PASSAGE),
        text=text,
        page=page,
        page_ordinal=ordinal,
        publisher=kw.pop("publisher", "U.S. Geological Survey"),
        doc_date=kw.pop("doc_date", "2016-07-12"),
        **kw,
    )
    unit.assign_id()
    return unit


@pytest.fixture()
def corpus(conn: sqlite3.Connection, register_file):
    """Two files, so cross-source leakage would be visible."""
    a = register_file("aaaaaaaa11111111", logical_path="corrosivity.pdf")
    b = register_file(
        "bbbbbbbb22222222",
        logical_path="nwqn.pdf",
        publisher="U.S. Environmental Protection Agency",
        doc_date="2020-12-02",
    )

    units.insert(
        conn,
        _unit(a, "corrosivity.pdf", 13, 0, "Dissolved solids ranged from 112 to 884 mg/L."),
    )
    units.insert(
        conn,
        _unit(
            a,
            "corrosivity.pdf",
            13,
            1,
            "Table 3. Summary of dissolved constituents.\nCalcium (mg/L) | Arsenic (µg/L)",
            unit_type=units.TABLE,
            caption="Table 3. Summary of dissolved constituents.",
            heading="Water-quality results",
        ),
    )
    units.insert(
        conn,
        _unit(a, "corrosivity.pdf", 14, 0, "Water temperature ranged up to 21.7 °C."),
    )
    units.insert(
        conn,
        _unit(
            b,
            "nwqn.pdf",
            7,
            0,
            "Figure 2. Map of sampling sites.",
            unit_type=units.FIGURE,
            caption="Figure 2. Map of sampling sites.",
            image_path="/work/images/abc.png",
            image_description="A choropleth map of the United States.",
        ),
    )
    conn.commit()
    return {"a": a, "b": b}


def test_search_returns_the_page_number_for_every_hit(
    conn: sqlite3.Connection, corpus
):
    hits = search_mod.search(conn, "mg/L")

    assert hits, "the query must match"
    for hit in hits:
        assert hit.page is not None
        assert isinstance(hit.page, int)
    assert {hit.page for hit in hits} == {13}


def test_search_carries_source_publisher_and_date(conn: sqlite3.Connection, corpus):
    hit = search_mod.search(conn, "dissolved solids")[0]

    assert hit.source_path == "corrosivity.pdf"
    assert hit.publisher == "U.S. Geological Survey"
    assert hit.doc_date == "2016-07-12"
    assert hit.page == 13
    assert hit.snippet


def test_a_unit_on_another_page_reports_that_page(conn: sqlite3.Connection, corpus):
    hit = search_mod.search(conn, "temperature")[0]
    assert hit.page == 14


def test_queries_with_punctuation_do_not_break_fts_syntax(
    conn: sqlite3.Connection, corpus
):
    """``mg/L``, ``(°C)`` and a bare quote are all legal user input."""
    for query in ("mg/L", "(°C)", 'dissolved "solids"', "21.7 °C", 'stray " quote'):
        search_mod.search(conn, query)  # must not raise


def test_unit_type_filter_narrows_results(conn: sqlite3.Connection, corpus):
    hits = search_mod.search(conn, "Summary of dissolved constituents", unit_type="table")
    assert hits
    assert all(hit.unit_type == "table" for hit in hits)


def test_captions_and_image_descriptions_are_searchable(
    conn: sqlite3.Connection, corpus
):
    assert search_mod.search(conn, "sampling sites"), "caption text must be indexed"
    hits = search_mod.search(conn, "choropleth")
    assert hits, "image descriptions must be indexed"
    assert hits[0].unit_type == "figure"


def test_read_exposes_full_provenance(conn: sqlite3.Connection, corpus):
    unit_id = search_mod.search(conn, "dissolved solids")[0].unit_id
    row = search_mod.get_unit(conn, unit_id)

    assert row["page"] == 13
    assert row["source_path"] == "corrosivity.pdf"
    assert row["sha256"]
    assert row["publisher"] == "U.S. Geological Survey"


def test_context_stays_within_one_page_of_one_file(conn: sqlite3.Connection, corpus):
    unit_id = search_mod.search(conn, "dissolved solids")[0].unit_id
    row = search_mod.get_unit(conn, unit_id)
    rows = search_mod.neighbours(conn, row)

    assert {r["file_id"] for r in rows} == {corpus["a"]}, "no other source may appear"
    assert {r["page"] for r in rows} == {13}
    assert [r["page_ordinal"] for r in rows] == [0, 1], "returned in reading order"


def test_unit_ids_are_stable_and_encode_their_page():
    first = _unit("aaaaaaaa11111111", "a.pdf", 13, 2, "x").unit_id
    second = _unit("aaaaaaaa11111111", "a.pdf", 13, 2, "x").unit_id

    assert first == second == "aaaaaaaa-p0013-002"


def test_an_ocr_unit_carries_its_page_confidence(
    conn: sqlite3.Connection, register_file
):
    """``recovered`` is not a claim of accuracy, so the number travels with the text."""
    file_id = register_file("dddddddd44444444", logical_path="scan.pdf")
    unit = _unit(file_id, "scan.pdf", 11, 0, "Papers on the conservation of water resources.")
    unit.from_ocr = True
    unit.ocr_confidence = 35.9
    units.insert(conn, unit)
    conn.commit()

    row = search_mod.get_unit(conn, unit.unit_id)
    assert row["from_ocr"] == 1
    assert row["ocr_confidence"] == pytest.approx(35.9)


def test_a_unit_with_no_pagination_reports_page_none(
    conn: sqlite3.Connection, register_file
):
    """Word documents have no fixed pages; that is recorded, not invented."""
    file_id = register_file("cccccccc33333333", logical_path="dataset.docx", media_type="docx")
    units.insert(conn, _unit(file_id, "dataset.docx", None, 0, "Enterococcus dPCR dataset."))
    conn.commit()

    hit = search_mod.search(conn, "Enterococcus")[0]
    assert hit.page is None


def test_reprocessing_a_file_leaves_no_stale_units(
    conn: sqlite3.Connection, corpus
):
    before = conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]
    removed = units.delete_for_file(conn, corpus["a"])
    conn.commit()

    assert removed == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"] == before - 3
    # The FTS index must shrink with it, or deleted text stays searchable.
    assert not search_mod.search(conn, "dissolved solids")
    assert search_mod.search(conn, "sampling sites"), "the other file is untouched"
