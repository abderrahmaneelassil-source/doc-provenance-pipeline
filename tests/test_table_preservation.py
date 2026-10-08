"""Table headers, units and captions must survive extraction verbatim.

A column heading is the only place a table records what its numbers mean. If
``Calcium, in milligrams per liter`` or ``Temperature (°C)`` is re-cased,
re-split or normalised on the way into the index, the measurements lose their
meaning — so these tests assert byte-exact preservation.
"""

from __future__ import annotations

import json

from dpp.config import Config
from dpp.extract.tables import ExtractedTable, cell_is_plausible, is_labelled, looks_like_table

HEADER_LINES = [
    "Site identifier    Calcium (mg/L)    Arsenic (µg/L)    Temperature (°C)    Discharge (kt)",
    "                   dissolved         dissolved         field               annual",
]

ROWS = [
    ["09380000", "41.3", "<1.0", "11.4", "1,230"],
    ["09402500", "112", "2.7", "21.7", "884"],
]

CAPTION = "Table 3. Dissolved constituents, in milligrams per liter, 2013–17."


def _table() -> ExtractedTable:
    return ExtractedTable(
        page_number=13,
        bbox=(50.0, 100.0, 560.0, 400.0),
        header_lines=list(HEADER_LINES),
        rows=[list(row) for row in ROWS],
        caption=CAPTION,
    )


def test_header_lines_are_kept_exactly_as_written():
    table = _table()
    assert table.header_lines == HEADER_LINES


def test_units_survive_in_the_readable_text():
    text = _table().readable_text()
    for unit in ("mg/L", "µg/L", "°C", "kt"):
        assert unit in text, f"{unit!r} must reach the search index unchanged"


def test_caption_is_verbatim_including_its_em_space_and_en_dash():
    text = _table().readable_text()
    assert CAPTION in text
    assert " " in text, "the publisher's em space is part of the caption"
    assert "2013–17" in text, "the en dash in the year range is not rewritten"


def test_readable_text_starts_with_caption_then_headers_then_rows():
    lines = _table().readable_text().splitlines()
    assert lines[0] == CAPTION
    assert lines[1:3] == HEADER_LINES
    assert lines[3] == "09380000 | 41.3 | <1.0 | 11.4 | 1,230"


def test_structured_rows_round_trip_through_json_unchanged():
    payload = json.loads(_table().rows_json())
    assert payload["header_lines"] == HEADER_LINES
    assert payload["rows"] == ROWS
    # Censored values and thousands separators are data, not noise.
    assert payload["rows"][0][2] == "<1.0"
    assert payload["rows"][0][4] == "1,230"


def test_both_representations_are_stored_together():
    table = _table()
    assert table.readable_text(), "a flat version for search"
    assert json.loads(table.rows_json())["rows"], "and structured rows for a reader"


def test_a_labelled_table_is_accepted_and_debris_is_not():
    assert is_labelled(CAPTION, [])
    assert is_labelled(None, HEADER_LINES)
    # Numeric debris: no caption, and a "header" with no words in it.
    assert not is_labelled(None, ["6,653 6,161 2,71 8,61 8,5"])
    assert not is_labelled(None, [])


def test_plausible_cells_cover_numbers_words_and_multi_value_cells():
    assert cell_is_plausible("41.3")
    assert cell_is_plausible("<1.0")
    assert cell_is_plausible("Alabama")
    assert cell_is_plausible(".3708 .3658 .3609 .3560")  # NACA-style multi-value cell
    # Reversed, chopped fragments from a bad OCR layer.
    assert not cell_is_plausible("esr")
    assert not cell_is_plausible(".sl")
    assert not cell_is_plausible("")


def test_the_quality_filter_keeps_a_real_grid(config: Config):
    assert looks_like_table([HEADER_LINES[:1] + [""], *ROWS], config) or looks_like_table(
        [list(r) for r in ROWS] * 2, config
    )


def test_the_quality_filter_rejects_shredded_prose(config: Config):
    prose = [
        ["Although DDE was", "detected most", "", "", ""],
        ["frequently and", "", "", "", ""],
        ["organochlorines", "", "", "", ""],
    ]
    assert looks_like_table(prose, config) is False
