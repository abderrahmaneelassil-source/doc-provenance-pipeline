"""Sparse-page detection, and the rule that decides whether OCR recovered anything.

These are the two judgements that keep the ledger honest, so they are tested as
pure functions — no Tesseract binary required.
"""

from __future__ import annotations

from pathlib import Path

import fitz
from conftest import make_pdf

from dpp.config import Config
from dpp.extract import ocr, pdf_text


def test_threshold_is_exclusive_at_the_boundary():
    assert pdf_text.is_sparse(119, 120) is True
    assert pdf_text.is_sparse(120, 120) is False
    assert pdf_text.is_sparse(121, 120) is False


def test_empty_page_is_sparse():
    assert pdf_text.is_sparse(0, 120) is True


def test_zero_threshold_disables_the_rule():
    assert pdf_text.is_sparse(0, 0) is False


def test_threshold_is_configurable():
    assert pdf_text.is_sparse(200, 500) is True
    assert pdf_text.is_sparse(200, 120) is False


def test_blank_and_text_pages_are_classified_from_a_real_pdf(tmp_path: Path):
    body = (
        "Specific conductance was measured in the field. Dissolved solids "
        "concentrations ranged from 112 to 884 mg/L across the sampled wells, "
        "and water temperature ranged from 9.4 to 21.7 degrees Celsius."
    )
    path = make_pdf(tmp_path / "mixed.pdf", [body, "", "Short."])

    doc = fitz.open(path)
    try:
        pages = [pdf_text.extract_page(doc[i]) for i in range(doc.page_count)]
    finally:
        doc.close()

    sparse = [p.page_number for p in pages if pdf_text.is_sparse(p.char_count, 120)]
    assert sparse == [2, 3], "the full page of prose is not sparse; the blank and short ones are"


def test_ocr_noise_is_not_substantive(config: Config):
    """A map page OCRs to a few stray glyphs. That is ``no_new_content``."""
    verdict = ocr.assess("A 7 ° , . N  x  12", native_chars=0, config=config)

    assert verdict.substantive is False
    assert verdict.word_count < config.ocr_min_words
    assert "words>=3chars=" in verdict.detail()


def test_ocr_prose_is_substantive(config: Config):
    recovered = (
        "Lahar is an Indonesian word for volcanic mudflow, a mixture of water, "
        "mud and volcanic rock flowing swiftly along a channel draining a volcano."
    )
    verdict = ocr.assess(recovered, native_chars=0, config=config)

    assert verdict.substantive is True
    assert verdict.gain_chars == len(recovered)


def test_ocr_must_add_characters_over_the_existing_text_layer(config: Config):
    """Re-reading what the text layer already had is not a recovery."""
    text = (
        "Specific conductance and dissolved solids were measured at every site "
        "during the sampling period described in this report."
    )
    # Plenty of real words, but the page already had that text.
    verdict = ocr.assess(text, native_chars=len(text) - 1, config=config)

    assert verdict.word_count >= config.ocr_min_words
    assert verdict.substantive is False, "no gain over the text layer means no new content"


def test_both_ocr_thresholds_are_configurable(config: Config):
    from dataclasses import replace

    text = "alpha beta gamma delta"  # 4 real words, 22 characters

    # Both thresholds must pass, so each one alone can veto the result.
    lenient = replace(config, ocr_min_words=4, ocr_min_gain_chars=10)
    assert ocr.assess(text, 0, lenient).substantive is True

    assert ocr.assess(text, 0, replace(lenient, ocr_min_words=99)).substantive is False
    assert ocr.assess(text, 0, replace(lenient, ocr_min_gain_chars=999)).substantive is False
