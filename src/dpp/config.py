"""Runtime configuration.

Every path is supplied by the caller; nothing machine-specific is baked in.
External binaries (Tesseract, LibreOffice) are located at run time in this order:
``PATH`` → environment variable → the conventional install locations for the
current platform. Which one was used is recorded in the ledger detail, so a run
is reproducible from its own output.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

#: Conventional install locations, by platform. Not machine-specific: these are
#: the documented defaults of the installers, and the user-scoped ones are
#: expanded from environment variables.
_TESSERACT_CANDIDATES = {
    "win32": (
        r"%ProgramFiles%\Tesseract-OCR\tesseract.exe",
        r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe",
        r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
    ),
    "darwin": ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"),
    "linux": ("/usr/bin/tesseract", "/usr/local/bin/tesseract"),
}

_SOFFICE_CANDIDATES = {
    "win32": (
        r"%ProgramFiles%\LibreOffice\program\soffice.exe",
        r"%ProgramFiles(x86)%\LibreOffice\program\soffice.exe",
    ),
    "darwin": ("/Applications/LibreOffice.app/Contents/MacOS/soffice",),
    "linux": ("/usr/bin/soffice", "/usr/local/bin/soffice", "/usr/bin/libreoffice"),
}


def _locate(exe_names: tuple[str, ...], env_var: str, candidates: dict[str, tuple[str, ...]]) -> str | None:
    """Find an external binary, or return ``None`` if it is not installed."""
    for name in exe_names:
        found = shutil.which(name)
        if found:
            return found
    override = os.environ.get(env_var)
    if override:
        # An explicit override that does not exist is a configuration error worth
        # surfacing rather than silently falling through to autodetection.
        if Path(override).exists():
            return override
        raise FileNotFoundError(f"{env_var} is set to {override!r}, which does not exist")
    for template in candidates.get(sys.platform, ()):
        expanded = Path(os.path.expandvars(template))
        if "%" not in str(expanded) and expanded.exists():
            return str(expanded)
    return None


def find_tesseract() -> str | None:
    return _locate(("tesseract",), "DPP_TESSERACT_CMD", _TESSERACT_CANDIDATES)


def find_soffice() -> str | None:
    return _locate(("soffice", "libreoffice"), "DPP_SOFFICE_CMD", _SOFFICE_CANDIDATES)


@dataclass(frozen=True)
class Config:
    """All tunable behaviour of the pipeline, in one place."""

    # --- paths -------------------------------------------------------------
    work_dir: Path
    """Everything the pipeline writes lives under here."""

    sources_file: Path | None = None
    """``SOURCES.md``-style provenance sidecar. Defaults to ``<input>/SOURCES.md``."""

    # --- sparse-page / OCR -------------------------------------------------
    sparse_min_chars: int = 120
    """A page with fewer than this many non-whitespace characters of extractable
    text is *sparse* and becomes an OCR candidate."""

    ocr_dpi: int = 300
    ocr_lang: str = "eng"

    ocr_min_words: int = 8
    """OCR output counts as substantive only with at least this many alphabetic
    words of 3+ characters. Below it the step is ``no_new_content`` — this is what
    stops map and plate pages from reporting recovery on OCR noise."""

    ocr_min_gain_chars: int = 40
    """OCR must also add at least this many characters over what the embedded
    text layer already gave for the same page."""

    # --- image / figure rules ---------------------------------------------
    image_min_px: int = 80
    """Skip images narrower or shorter than this, in pixels."""

    image_min_area_frac: float = 0.01
    """Skip images covering less than this fraction of the page area."""

    image_max_area_frac: float = 0.75
    """Skip images covering *more* than this fraction of the page: in a scanned
    document that is the page scan itself, not a figure. Raise it towards 1.0 to
    keep full-page plates and maps, at the cost of one figure unit per scanned
    page."""

    image_max_aspect: float = 20.0
    """Skip extreme slivers (rules, borders, gradient strips)."""

    caption_window_pt: float = 90.0
    """How far above/below a figure to look for its caption, in PDF points."""

    context_window_pt: float = 160.0
    """How far above/below a figure to gather surrounding text, in PDF points."""

    # --- table rules -------------------------------------------------------
    table_strategy: str = "text"
    """``"text"`` detects columns from whitespace alignment, ``"lines"`` from
    ruling lines. See :mod:`dpp.extract.tables` for why ``text`` is the default."""

    table_min_rows: int = 2
    table_min_cols: int = 2

    table_min_filled_row_frac: float = 0.5
    """At least this fraction of rows must fill ``table_min_cols`` cells, which
    is what separates a real grid from aligned prose."""

    table_min_col_fill: float = 0.6
    """A column counts as real when this fraction of rows populate it. A grid
    needs ``table_min_cols`` such columns."""

    table_max_mean_cell_chars: float = 80.0
    """Candidate grids whose mean non-empty cell is longer than this are prose."""

    table_require_label: bool = True
    """Only keep tables that carry a caption or named columns. See
    :func:`dpp.extract.tables.is_labelled`."""

    table_min_plausible_cell_frac: float = 0.7
    """Fraction of non-empty cells that must read as real values. Below it the
    band is rejected rather than stored: cell geometry taken from a poor OCR
    layer produces reversed, chopped cells, and a garbled table in the index is
    worse than an acknowledged gap in it."""

    table_require_candidate: bool = True
    """Only hand pages to pdfplumber when a cheap PyMuPDF pass says they could
    hold a table. Set False to scan every page — far slower on documents with
    large vector maps."""

    table_numeric_line_min: int = 8
    """Lines of mostly-numeric tokens needed to call a page a table candidate."""

    table_numeric_token_frac: float = 0.6
    """Fraction of a line's tokens that must be numeric for it to count."""

    table_header_lines_max: int = 4
    """How many lines above a run of data rows to pull in as column headers.
    These carry the units, so they must be inside the extracted band."""

    table_overlap_frac: float = 0.6
    """A text block overlapping a detected table by more than this fraction is
    suppressed as a passage, so table content is not indexed twice."""

    # --- heading detection -------------------------------------------------
    heading_size_ratio: float = 1.12
    """A line whose font is this much larger than the body font is a heading."""

    heading_max_chars: int = 140

    repair_shredded_lines: bool = True
    """Rebuild lines from word geometry on pages whose text layer lost its line
    structure. See :func:`dpp.extract.pdf_text.repair_shredded_lines`."""

    shredded_single_word_frac: float = 0.6
    """Fraction of single-word lines above which a page counts as shredded."""

    # --- optional vision description --------------------------------------
    describe_images: bool = False
    """OFF by default: the pipeline makes no API calls and costs nothing to run."""

    describe_model: str = "claude-opus-5"
    describe_max_images: int | None = None

    # --- limits ------------------------------------------------------------
    max_pages: int | None = None
    """Cap pages per document. Useful for smoke runs; ``None`` means no cap."""

    archive_max_depth: int = 2
    """How deep to descend into nested archives."""

    # --- resolved at construction -----------------------------------------
    tesseract_cmd: str | None = field(default=None)
    soffice_cmd: str | None = field(default=None)

    def resolved(self) -> Config:
        """Return a copy with external binaries located."""
        return replace(
            self,
            work_dir=self.work_dir.expanduser().resolve(),
            tesseract_cmd=self.tesseract_cmd or find_tesseract(),
            soffice_cmd=self.soffice_cmd or find_soffice(),
        )

    # --- derived paths -----------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.work_dir / "ledger.sqlite3"

    @property
    def images_dir(self) -> Path:
        """Content-addressed store for preserved originals."""
        return self.work_dir / "images"

    @property
    def extracted_dir(self) -> Path:
        """Where archive members and converted Office files are unpacked."""
        return self.work_dir / "extracted"

    def ensure_dirs(self) -> None:
        for path in (self.work_dir, self.images_dir, self.extracted_dir):
            path.mkdir(parents=True, exist_ok=True)
