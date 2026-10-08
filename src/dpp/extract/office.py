"""Word documents: ``.docx`` directly, ``.doc`` via LibreOffice.

Two things differ from the PDF path and both are recorded rather than papered
over:

* **There are no page numbers.** A ``.docx`` stores a flow of content, and where
  the page breaks fall depends on the renderer. Units from Word documents carry
  ``page = None``, which the CLI shows as ``n/a``. Inventing a page number would
  be worse than admitting there isn't one.
* **``.doc`` is not readable without a converter.** If LibreOffice is not
  installed the file is recorded ``source_dependent`` — the content is not lost,
  it is unavailable until someone exports it or supplies a replacement.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import docx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

from ..config import Config

#: Conversion is bounded: a stuck headless LibreOffice must not hang a build.
CONVERT_TIMEOUT_S = 180


class ConversionUnavailable(RuntimeError):
    """Raised when a ``.doc`` needs LibreOffice and none could be found."""


@dataclass(frozen=True)
class OfficeBlock:
    """One paragraph or table from a Word document, in document order."""

    ordinal: int
    kind: str
    """``"passage"`` or ``"table"``."""

    text: str
    heading: str | None
    rows: list[list[str]] | None = None
    header_lines: list[str] | None = None


def _iter_body(document: docx.document.Document):
    """Walk paragraphs and tables in the order they appear.

    python-docx exposes the two as separate collections, which loses their
    interleaving; the underlying XML keeps it.
    """
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield DocxTable(child, document)


def _is_heading(paragraph: Paragraph) -> bool:
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    return style.startswith("Heading") or style == "Title" or style == "Subtitle"


def _table_text(rows: list[list[str]], header_lines: list[str]) -> str:
    lines = list(header_lines)
    lines.extend(" | ".join(row) for row in rows)
    return "\n".join(lines).strip()


def read_docx(path: Path) -> list[OfficeBlock]:
    """Read a ``.docx`` into ordered blocks, carrying the nearest heading."""
    document = docx.Document(str(path))
    blocks: list[OfficeBlock] = []
    heading: str | None = None
    ordinal = 0

    for item in _iter_body(document):
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            if _is_heading(item):
                heading = text
            blocks.append(
                OfficeBlock(ordinal=ordinal, kind="passage", text=text, heading=heading)
            )
            ordinal += 1
            continue

        # A table: Word gives real cells, so the header row is trustworthy here
        # in a way it never is for a PDF.
        rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        header_lines = [" | ".join(rows[0])] if len(rows) > 1 else []
        body_rows = rows[1:] if len(rows) > 1 else rows
        blocks.append(
            OfficeBlock(
                ordinal=ordinal,
                kind="table",
                text=_table_text(body_rows, header_lines),
                heading=heading,
                rows=body_rows,
                header_lines=header_lines,
            )
        )
        ordinal += 1

    return blocks


def convert_doc(path: Path, config: Config) -> Path:
    """Convert a legacy ``.doc`` to ``.docx`` with headless LibreOffice.

    Raises :class:`ConversionUnavailable` when no converter exists, which the
    pipeline turns into a ``source_dependent`` ledger row.
    """
    if not config.soffice_cmd:
        raise ConversionUnavailable(
            "legacy .doc requires LibreOffice; none found on PATH, in "
            "DPP_SOFFICE_CMD, or at a conventional install location"
        )

    target = config.extracted_dir / "converted"
    target.mkdir(parents=True, exist_ok=True)

    # LibreOffice refuses to run two instances against one profile, so give each
    # conversion its own throwaway user profile directory.
    profile = target / f".profile-{path.stem}"
    completed = subprocess.run(
        [
            config.soffice_cmd,
            f"-env:UserInstallation=file:///{profile.as_posix().lstrip('/')}",
            "--headless",
            "--norestore",
            "--convert-to",
            "docx",
            "--outdir",
            str(target),
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=CONVERT_TIMEOUT_S,
    )

    produced = target / (path.stem + ".docx")
    if not produced.is_file():
        raise ConversionUnavailable(
            f"LibreOffice produced no .docx (exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout or '').strip()[:300]}"
        )
    shutil.rmtree(profile, ignore_errors=True)
    return produced
