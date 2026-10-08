"""OCR for sparse pages only, and the judgement of whether it recovered anything.

Two things matter here beyond calling Tesseract:

1. OCR runs **only** on pages the sparse rule flagged. Re-OCRing a page that
   already has a good text layer costs seconds per page and gains nothing.

2. Running is not recovering. A full-page map OCRs to a handful of stray glyphs;
   a scanned page of prose OCRs to paragraphs. :func:`assess` separates the two
   and is what lets the ledger say ``no_new_content`` honestly instead of
   claiming a win for noise.

Word boxes come back from Tesseract, so OCR'd text lands in the same
line/block/page shape as native text and inherits heading detection and caption
matching for free.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import fitz
import pytesseract
from PIL import Image

from ..config import Config
from .layout import Block, Line, PageText

#: Alphabetic runs of 3+ characters — the unit in which we judge "real words".
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


class TesseractUnavailable(RuntimeError):
    """Raised when OCR is needed but no Tesseract binary could be found."""


@dataclass(frozen=True)
class OcrAssessment:
    """Whether an OCR pass produced substantive new text, and the numbers why."""

    substantive: bool
    ocr_chars: int
    native_chars: int
    word_count: int

    @property
    def gain_chars(self) -> int:
        return self.ocr_chars - self.native_chars

    def detail(self) -> str:
        return (
            f"ocr_chars={self.ocr_chars} native_chars={self.native_chars} "
            f"gain={self.gain_chars} words>=3chars={self.word_count}"
        )


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def assess(ocr_text: str, native_chars: int, config: Config) -> OcrAssessment:
    """Decide whether OCR output counts as recovered content.

    Both tests must pass: enough real words to not be glyph noise, and enough
    additional characters to be more than the text layer already had.
    """
    stripped = ocr_text.strip()
    words = word_count(stripped)
    substantive = (
        words >= config.ocr_min_words
        and (len(stripped) - native_chars) >= config.ocr_min_gain_chars
    )
    return OcrAssessment(
        substantive=substantive,
        ocr_chars=len(stripped),
        native_chars=native_chars,
        word_count=words,
    )


def configure(config: Config) -> None:
    """Point pytesseract at the located binary."""
    if not config.tesseract_cmd:
        raise TesseractUnavailable(
            "no tesseract binary on PATH, in DPP_TESSERACT_CMD, or at a conventional "
            "install location; install Tesseract or set DPP_TESSERACT_CMD"
        )
    pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd


def render(page: fitz.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def ocr_page(page: fitz.Page, config: Config) -> PageText:
    """OCR one page into the shared layout types.

    Tesseract reports geometry in image pixels; it is scaled back to PDF points
    so bounding boxes are comparable with everything extracted natively.
    """
    configure(config)
    image = render(page, config.ocr_dpi)
    data = pytesseract.image_to_data(
        image, lang=config.ocr_lang, output_type=pytesseract.Output.DICT
    )

    scale = 72.0 / config.ocr_dpi
    # Group words into lines, and lines into paragraphs, using Tesseract's own
    # layout analysis rather than guessing from coordinates.
    paragraphs: dict[tuple[int, int], dict[int, list[tuple[str, tuple[float, ...], float]]]] = {}
    confidences: list[float] = []

    for index, raw_word in enumerate(data["text"]):
        word = raw_word.strip()
        if not word:
            continue
        confidence = float(data["conf"][index])
        if confidence >= 0:
            confidences.append(confidence)
        left = data["left"][index] * scale
        top = data["top"][index] * scale
        right = left + data["width"][index] * scale
        bottom = top + data["height"][index] * scale
        key = (data["block_num"][index], data["par_num"][index])
        paragraphs.setdefault(key, {}).setdefault(data["line_num"][index], []).append(
            (word, (left, top, right, bottom), confidence)
        )

    result = PageText(
        page_number=page.number + 1,
        width=float(page.rect.width),
        height=float(page.rect.height),
        from_ocr=True,
        ocr_confidence=sum(confidences) / len(confidences) if confidences else None,
    )

    for key in sorted(paragraphs):
        lines: list[Line] = []
        for line_num in sorted(paragraphs[key]):
            words = paragraphs[key][line_num]
            boxes = [box for _, box, _ in words]
            bbox = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            lines.append(
                Line(
                    text=" ".join(word for word, _, _ in words),
                    bbox=bbox,
                    # No font metadata from OCR: line height stands in for size,
                    # which is enough for the relative comparison in heading
                    # detection.
                    size=round(bbox[3] - bbox[1], 1),
                )
            )
        if not lines:
            continue
        result.blocks.append(
            Block(
                lines=tuple(lines),
                bbox=(
                    min(line.bbox[0] for line in lines),
                    min(line.bbox[1] for line in lines),
                    max(line.bbox[2] for line in lines),
                    max(line.bbox[3] for line in lines),
                ),
            )
        )

    return result
