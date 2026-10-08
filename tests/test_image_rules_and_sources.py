"""The decorative-image rule, figure deduplication, and the provenance sidecar."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dpp.config import Config
from dpp.extract.images import ExtractedImage, find_caption, is_figure_caption, skip_reason
from dpp.extract.layout import Block, Line, PageText, extend_caption
from dpp.sources import display, parse

PAGE = PageText(page_number=5, width=612.0, height=792.0)


def _full_page_box() -> tuple[float, float, float, float]:
    return (56.0, 56.0, 556.0, 500.0)


def test_a_real_figure_is_kept(config: Config):
    assert skip_reason(_full_page_box(), 1200, 900, PAGE, config) is None


def test_a_tiny_logo_is_skipped_by_the_pixel_rule(config: Config):
    reason = skip_reason((10.0, 10.0, 40.0, 40.0), 32, 32, PAGE, config)
    assert reason is not None
    assert reason.rule == "image_min_px"
    assert "image_min_px=80" in reason.detail


def test_a_small_placement_is_skipped_by_the_area_rule(config: Config):
    """Large enough in pixels, but it covers almost none of the page."""
    reason = skip_reason((10.0, 10.0, 26.0, 26.0), 400, 400, PAGE, config)
    assert reason is not None
    assert reason.rule == "image_min_area_frac"
    assert "image_min_area_frac=0.01" in reason.detail


def test_a_full_page_scan_is_skipped_as_not_a_figure(config: Config):
    """In a scanned document every page is one big image; those are not figures."""
    reason = skip_reason((0.0, 0.0, 612.0, 792.0), 2550, 3300, PAGE, config)
    assert reason is not None
    assert reason.rule == "image_max_area_frac"
    assert "page scan" in reason.detail


def test_raising_the_ceiling_keeps_full_page_plates(config: Config):
    relaxed = replace(config, image_max_area_frac=1.0)
    assert skip_reason((0.0, 0.0, 612.0, 792.0), 2550, 3300, PAGE, relaxed) is None


def test_a_rule_or_gradient_strip_is_skipped_by_the_aspect_rule(config: Config):
    reason = skip_reason((56.0, 56.0, 556.0, 300.0), 4000, 100, PAGE, config)
    assert reason is not None
    assert reason.rule == "image_max_aspect"


def test_every_threshold_is_configurable(config: Config):
    box, width, height = (10.0, 10.0, 40.0, 40.0), 32, 32
    assert skip_reason(box, width, height, PAGE, config) is not None

    relaxed = replace(config, image_min_px=8, image_min_area_frac=0.0)
    assert skip_reason(box, width, height, PAGE, relaxed) is None


def test_skip_reasons_always_name_their_rule_and_threshold(config: Config):
    reasons = [
        skip_reason((10.0, 10.0, 40.0, 40.0), 32, 32, PAGE, config),
        skip_reason((10.0, 10.0, 26.0, 26.0), 400, 400, PAGE, config),
        skip_reason((56.0, 56.0, 556.0, 300.0), 4000, 100, PAGE, config),
    ]
    for reason in reasons:
        assert reason is not None
        assert reason.rule in reason.detail or "=" in reason.detail


def test_a_figure_unit_has_no_description_when_none_was_generated():
    figure = ExtractedImage(
        page_number=5,
        bbox=_full_page_box(),
        image_sha256="a" * 64,
        image_path=Path("/work/images/aaaa.png"),
        width=1200,
        height=900,
        caption="Figure 3. Map of potentially corrosive groundwater.",
        context="Prevalence is highest in the Northeast.",
    )

    text = figure.searchable_text(description=None)
    assert "Figure 3." in text
    assert "Northeast" in text

    described = figure.searchable_text("A choropleth map of the United States.")
    assert "choropleth" in described
    assert len(described) > len(text)


# --------------------------------------------------------------------------
# wrapped captions
# --------------------------------------------------------------------------


def _page_with_lines(texts: list[str], start_top: float = 520.0, leading: float = 12.0):
    """A page whose lines sit one under another with normal leading."""
    lines = [
        Line(text=text, bbox=(56.0, start_top + i * leading, 400.0, start_top + i * leading + 10.0), size=9.0)
        for i, text in enumerate(texts)
    ]
    page = PageText(page_number=2, width=612.0, height=792.0)
    page.blocks = [Block(lines=tuple(lines), bbox=(56.0, lines[0].top, 400.0, lines[-1].bottom))]
    return page, lines


def test_a_wrapped_caption_is_joined_across_its_lines():
    page, lines = _page_with_lines(
        [
            "Figure 3.  Climatic water deficit (CWD) accumulated through water-",
            "year 2015 for the Sacramento River basin.",
        ]
    )

    joined = extend_caption(page, lines[0], is_figure_caption)
    # Joined without inserting a space, and the publisher's hyphen is kept:
    # "water-year" is a real compound, and de-hyphenating would rewrite it.
    assert joined == (
        "Figure 3.  Climatic water deficit (CWD) accumulated through "
        "water-year 2015 for the Sacramento River basin."
    )


def test_a_soft_hyphen_break_keeps_the_hyphen_rather_than_guessing():
    """``concen-`` + ``tration`` is ambiguous; the document's text is preserved."""
    page, lines = _page_with_lines(
        ["Table 7.  Calcium concen-", "tration in milligrams per liter."]
    )

    joined = extend_caption(page, lines[0], lambda text: text.startswith("Table "))
    assert joined == "Table 7.  Calcium concen-tration in milligrams per liter."
    assert "milligrams per liter" in joined


def test_caption_extension_stops_at_the_next_caption():
    page, lines = _page_with_lines(
        [
            "Figure 3.  First caption, which continues",
            "onto a second line.",
            "Figure 4.  A different figure entirely.",
        ]
    )

    joined = extend_caption(page, lines[0], is_figure_caption)
    assert "second line." in joined
    assert "Figure 4." not in joined


def test_caption_extension_stops_at_a_vertical_gap():
    page = PageText(page_number=2, width=612.0, height=792.0)
    first = Line(text="Figure 3.  A caption.", bbox=(56.0, 520.0, 400.0, 530.0), size=9.0)
    far = Line(text="Unrelated body text.", bbox=(56.0, 700.0, 400.0, 710.0), size=9.0)
    page.blocks = [
        Block(lines=(first,), bbox=(56.0, 520.0, 400.0, 530.0)),
        Block(lines=(far,), bbox=(56.0, 700.0, 400.0, 710.0)),
    ]

    assert extend_caption(page, first, is_figure_caption) == "Figure 3.  A caption."


def test_caption_extension_stays_in_its_own_column():
    """On a two-column page the next line down belongs to the other column."""
    page = PageText(page_number=2, width=612.0, height=792.0)
    left = [
        Line(text="Figure 3.  Climatic water deficit accumulated through water-", bbox=(56.0, 520.0, 290.0, 530.0), size=8.0),
        Line(text="year 2015 as a percentage of the baseline.", bbox=(56.0, 532.0, 290.0, 542.0), size=8.0),
    ]
    right = [
        Line(text="2015 could become more the norm than the exception", bbox=(320.0, 524.0, 556.0, 534.0), size=9.0),
        Line(text="in years to come.", bbox=(320.0, 536.0, 556.0, 546.0), size=9.0),
    ]
    page.blocks = [
        Block(lines=tuple(left), bbox=(56.0, 520.0, 290.0, 542.0)),
        Block(lines=tuple(right), bbox=(320.0, 524.0, 556.0, 546.0)),
    ]

    joined = extend_caption(page, left[0], is_figure_caption)
    assert joined == (
        "Figure 3.  Climatic water deficit accumulated through "
        "water-year 2015 as a percentage of the baseline."
    )
    assert "could become more the norm" not in joined


def test_find_caption_prefers_the_line_below_the_figure():
    page, lines = _page_with_lines(
        ["Figure 2.  Above the image.", "Figure 3.  Below the image."],
        start_top=100.0,
        leading=400.0,
    )
    # Image sits between the two caption lines.
    caption = find_caption(page, (56.0, 150.0, 400.0, 460.0), window_pt=90.0)
    assert caption is not None
    assert caption.startswith("Figure 3.")


# --------------------------------------------------------------------------
# SOURCES.md
# --------------------------------------------------------------------------

SIDECAR = """# Sample sources

Prose that the parser must ignore.

## report.pdf
- title: Potential corrosivity of untreated groundwater
- publisher: U.S. Geological Survey
- date: 2016-07-12
- source_url: https://pubs.usgs.gov/sir/2016/5092/sir20165092.pdf
- license: Public domain — work of the U.S. Government
- notes: text-based PDF; tables carry units (mg/L)

## form.doc
- publisher: U.S. Fish and Wildlife Service
- date: unknown

## bundle.zip!/factsheet.pdf
- publisher: U.S. Geological Survey
- date: 2013
"""


def test_entries_are_parsed_with_their_values_intact():
    records = parse(SIDECAR)

    assert set(records) == {"report.pdf", "form.doc", "bundle.zip!/factsheet.pdf"}
    report = records["report.pdf"]
    assert report.publisher == "U.S. Geological Survey"
    assert report.date == "2016-07-12"
    assert report.source_url.endswith("sir20165092.pdf")
    assert "mg/L" in report.notes
    assert "—" in report.license, "the em dash in the licence text is preserved"


def test_an_explicit_unknown_is_treated_as_absent_not_as_the_word():
    record = parse(SIDECAR)["form.doc"]

    assert record.date is None, "nothing is invented, and 'unknown' is not stored as a value"
    assert record.publisher == "U.S. Fish and Wildlife Service"
    assert display(record.date) == "unknown", "but it renders as unknown for the reader"


def test_archive_members_are_addressable():
    assert parse(SIDECAR)["bundle.zip!/factsheet.pdf"].date == "2013"


def test_a_missing_or_absent_sidecar_yields_no_metadata(tmp_path: Path):
    from dpp.sources import load

    assert load(None) == {}
    assert load(tmp_path / "nope.md") == {}


def test_unknown_keys_and_stray_bullets_are_ignored():
    records = parse("## a.pdf\n- publisher: X\n- bogus: Y\n- not a field\n")
    assert records["a.pdf"].publisher == "X"
    assert records["a.pdf"].title is None
