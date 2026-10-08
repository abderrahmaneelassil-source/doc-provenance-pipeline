"""The pipeline: inventory → text/OCR → headings → tables → figures → index.

Each file is processed independently and each stage writes its own ledger row,
so a file that fails at one stage still keeps whatever earlier stages recovered.
Exceptions are caught per stage and recorded as ``failed`` with their message —
a crash in one document never ends the run.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pdfplumber

from . import inventory, ledger, sources, units
from .config import Config
from .describe import DescriptionUnavailable, Describer
from .extract import headings as headings_mod
from .extract import images as images_mod
from .extract import layout, ocr, office, pdf_text
from .extract import tables as tables_mod
from .inventory import InventoryItem


@dataclass
class RunReport:
    """What one ``build`` or ``refresh`` did."""

    files_seen: int = 0
    files_processed: int = 0
    files_unchanged: int = 0
    units_written: int = 0
    pages_ocred: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.files_seen} files seen, {self.files_processed} processed, "
            f"{self.files_unchanged} unchanged, {self.units_written} units written, "
            f"{self.pages_ocred} pages OCR'd"
        )


def run(
    input_dir: Path,
    config: Config,
    conn: sqlite3.Connection,
    *,
    only_changed: bool = False,
    describer: Describer | None = None,
) -> RunReport:
    """Process ``input_dir``. With ``only_changed``, skip files whose hash matches."""
    config.ensure_dirs()
    metadata = sources.load(config.sources_file or (input_dir / "SOURCES.md"))
    items = inventory.walk(input_dir, config, metadata)
    report = RunReport(files_seen=len(items))

    for item in items:
        previous_sha = inventory.upsert(conn, item)
        if only_changed and previous_sha == item.sha256:
            report.files_unchanged += 1
            continue

        inventory.record_inventory_step(conn, item)
        # Reprocessing replaces a file's units wholesale rather than merging, so
        # a shrunken or re-typeset document cannot leave orphans behind.
        units.delete_for_file(conn, item.file_id)

        try:
            _process(item, config, conn, report, describer)
        except Exception as exc:  # noqa: BLE001 - the ledger is the error channel
            ledger.record_failure(conn, item.file_id, "pipeline", exc)
            report.notes.append(f"{item.logical_path}: {type(exc).__name__}: {exc}")

        conn.execute(
            "UPDATE files SET last_processed = ? WHERE file_id = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), item.file_id),
        )
        report.files_processed += 1
        conn.commit()

    conn.commit()
    return report


def _process(
    item: InventoryItem,
    config: Config,
    conn: sqlite3.Connection,
    report: RunReport,
    describer: Describer | None,
) -> None:
    if item.media_type == inventory.PDF:
        _process_pdf(item, config, conn, report, describer)
    elif item.media_type == inventory.DOCX:
        _process_docx(item, item.stored_path, config, conn, report)
    elif item.media_type == inventory.DOC:
        _process_doc(item, config, conn, report)
    elif item.media_type == inventory.ZIP:
        # Members were inventoried as their own files; the archive itself holds
        # no extractable text of its own.
        members = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE parent_archive_id = ?", (item.file_id,)
        ).fetchone()["n"]
        ledger.record_outcome(
            conn,
            item.file_id,
            ledger.STEP_ARCHIVE,
            recovered_count=members,
            detail=f"{members} member(s) expanded and inventoried separately",
        )
    else:
        ledger.record_skipped(
            conn,
            item.file_id,
            ledger.STEP_TEXT,
            f"no extractor for media type '{item.media_type}'",
        )


# --------------------------------------------------------------------------
# PDFs
# --------------------------------------------------------------------------


def _process_pdf(
    item: InventoryItem,
    config: Config,
    conn: sqlite3.Connection,
    report: RunReport,
    describer: Describer | None,
) -> None:
    doc = fitz.open(item.stored_path)
    try:
        limit = doc.page_count if config.max_pages is None else min(doc.page_count, config.max_pages)

        pages, repaired = _read_pages(doc, limit, config)

        # --- text extraction + OCR on sparse pages only -------------------
        sparse = [p for p in pages if pdf_text.is_sparse(p.char_count, config.sparse_min_chars)]
        native_pages = sum(1 for p in pages if p.char_count)
        ledger.record_outcome(
            conn,
            item.file_id,
            ledger.STEP_TEXT,
            recovered_count=native_pages,
            detail=(
                f"{native_pages}/{limit} pages had extractable text; "
                f"{len(sparse)} sparse (<{config.sparse_min_chars} chars); "
                f"{repaired} page(s) needed line-structure repair"
            ),
        )

        if sparse:
            _ocr_sparse_pages(doc, pages, sparse, item, config, conn, report)

        # Computed after OCR, so pages whose text came from OCR contribute to
        # the body-size estimate and get headings of their own.
        body_size = pdf_text.body_font_size(pages)
        heading_count = headings_mod.count_headings(pages, body_size, config)
        ledger.record_outcome(
            conn,
            item.file_id,
            ledger.STEP_HEADINGS,
            recovered_count=heading_count,
            detail=f"{heading_count} distinct heading(s); body font {body_size}pt",
        )

        # --- tables --------------------------------------------------------
        page_tables = _extract_tables(item, pages, config, conn)

        # --- figures -------------------------------------------------------
        page_figures = _extract_figures(doc, pages, item, config, conn)

        # --- units ---------------------------------------------------------
        written = _write_pdf_units(
            item, pages, body_size, page_tables, page_figures, config, conn, describer
        )
        report.units_written += written
        ledger.record_outcome(
            conn,
            item.file_id,
            ledger.STEP_INDEX,
            recovered_count=written,
            detail=f"{written} unit(s) indexed",
        )
    finally:
        doc.close()


def _read_pages(doc: fitz.Document, limit: int, config: Config) -> tuple[list[layout.PageText], int]:
    pages: list[layout.PageText] = []
    repaired = 0
    for index in range(limit):
        page = pdf_text.extract_page(doc[index])
        if config.repair_shredded_lines and pdf_text.is_shredded(
            page, config.shredded_single_word_frac
        ):
            page = pdf_text.repair_shredded_lines(page)
            repaired += 1
        pages.append(page)
    return pages, repaired


def _ocr_sparse_pages(
    doc: fitz.Document,
    pages: list[layout.PageText],
    sparse: list[layout.PageText],
    item: InventoryItem,
    config: Config,
    conn: sqlite3.Connection,
    report: RunReport,
) -> None:
    """OCR each sparse page, replacing it only when OCR recovered real text.

    The ``recovered`` / ``no_new_content`` split is the whole point: a scanned
    page of prose and a full-page map both run OCR, and only one of them
    recovers anything.
    """
    try:
        ocr.configure(config)
    except ocr.TesseractUnavailable as exc:
        ledger.record_source_dependent(
            conn,
            item.file_id,
            ledger.STEP_OCR,
            f"{len(sparse)} sparse page(s) need OCR but {exc}",
        )
        return

    recovered_pages = 0
    details: list[str] = []
    index_by_page = {page.page_number: position for position, page in enumerate(pages)}

    for page in sparse:
        try:
            result = ocr.ocr_page(doc[page.page_number - 1], config)
        except Exception as exc:  # noqa: BLE001 - one bad page must not end the file
            details.append(f"p{page.page_number}: {type(exc).__name__}: {exc}")
            continue

        verdict = ocr.assess(result.text, page.char_count, config)
        confidence = (
            f" mean_conf={result.ocr_confidence:.1f}%" if result.ocr_confidence is not None else ""
        )
        details.append(
            f"p{page.page_number}: {'recovered' if verdict.substantive else 'no_new_content'} "
            f"({verdict.detail()}{confidence})"
        )
        report.pages_ocred += 1

        if verdict.substantive:
            pages[index_by_page[page.page_number]] = result
            recovered_pages += 1

    ledger.record_outcome(
        conn,
        item.file_id,
        ledger.STEP_OCR,
        recovered_count=recovered_pages,
        detail=(
            f"OCR ran on {len(sparse)} sparse page(s) at {config.ocr_dpi}dpi, "
            f"recovered substantive text on {recovered_pages}; " + "; ".join(details[:40])
        ),
    )


def _extract_tables(
    item: InventoryItem,
    pages: list[layout.PageText],
    config: Config,
    conn: sqlite3.Connection,
) -> dict[int, list[tables_mod.ExtractedTable]]:
    candidates = [p for p in pages if tables_mod.is_table_candidate(p, config)]
    found: dict[int, list[tables_mod.ExtractedTable]] = {}
    rejected = 0

    if not candidates:
        ledger.record_outcome(
            conn,
            item.file_id,
            ledger.STEP_TABLES,
            recovered_count=0,
            detail="no page matched the table-candidate rule (no table caption, no numeric rows)",
        )
        return found

    try:
        with pdfplumber.open(item.stored_path) as pdf:
            for page in candidates:
                plumber_page = pdf.pages[page.page_number - 1]
                try:
                    page_tables, page_rejected = tables_mod.extract_page_tables(
                        plumber_page, page, config
                    )
                finally:
                    plumber_page.close()
                rejected += page_rejected
                if page_tables:
                    found[page.page_number] = page_tables
    except Exception as exc:  # noqa: BLE001
        ledger.record_failure(conn, item.file_id, ledger.STEP_TABLES, exc)
        return found

    total = sum(len(v) for v in found.values())
    ledger.record_outcome(
        conn,
        item.file_id,
        ledger.STEP_TABLES,
        recovered_count=total,
        detail=(
            f"{total} table(s) from {len(candidates)} candidate page(s) "
            f"(of {len(pages)}); {rejected} band(s) rejected by the quality rule"
        ),
    )
    if rejected:
        ledger.record_skipped(
            conn,
            item.file_id,
            ledger.STEP_TABLES,
            f"{rejected} detected table band(s) dropped: unlabelled, or under "
            f"table_min_plausible_cell_frac={config.table_min_plausible_cell_frac} "
            f"(cell text unreadable, typically a poor OCR layer)",
        )
    return found


def _extract_figures(
    doc: fitz.Document,
    pages: list[layout.PageText],
    item: InventoryItem,
    config: Config,
    conn: sqlite3.Connection,
) -> dict[int, list[images_mod.ExtractedImage]]:
    found: dict[int, list[images_mod.ExtractedImage]] = {}
    seen: dict[str, Path] = {}
    skips: list[images_mod.SkipReason] = []
    repeats = 0

    for page in pages:
        try:
            figures, page_skips, page_repeats = images_mod.extract_page_images(
                doc[page.page_number - 1], page, config, seen
            )
        except Exception as exc:  # noqa: BLE001
            ledger.record_failure(
                conn, item.file_id, ledger.STEP_IMAGES, exc, f"page {page.page_number}:"
            )
            continue
        skips.extend(page_skips)
        repeats += page_repeats
        if figures:
            found[page.page_number] = figures

    total = sum(len(v) for v in found.values())
    ledger.record_outcome(
        conn,
        item.file_id,
        ledger.STEP_IMAGES,
        recovered_count=total,
        detail=(
            f"{total} figure(s) kept, {repeats} repeat placement(s) deduplicated "
            f"by image hash, {len(skips)} skipped by rule"
        ),
    )

    if skips:
        by_rule: dict[str, int] = {}
        examples: dict[str, str] = {}
        for skip in skips:
            by_rule[skip.rule] = by_rule.get(skip.rule, 0) + 1
            examples.setdefault(skip.rule, skip.detail)
        summary = "; ".join(
            f"{rule} x{count} (e.g. {examples[rule]})" for rule, count in sorted(by_rule.items())
        )
        ledger.record_skipped(conn, item.file_id, ledger.STEP_IMAGES, summary)

    return found


def _write_pdf_units(
    item: InventoryItem,
    pages: list[layout.PageText],
    body_size: float,
    page_tables: dict[int, list[tables_mod.ExtractedTable]],
    page_figures: dict[int, list[images_mod.ExtractedImage]],
    config: Config,
    conn: sqlite3.Connection,
    describer: Describer | None,
) -> int:
    """Emit passage, table and figure units for one PDF, in reading order."""
    written = 0
    described = 0
    describe_errors: list[str] = []
    assigned = headings_mod.assign_headings(pages, body_size, config)

    for page in pages:
        ordinal = 0
        per_block = assigned.get(page.page_number, {})
        table_boxes = [t.bbox for t in page_tables.get(page.page_number, [])]

        for index, block in enumerate(page.blocks):
            text = block.text
            if not text:
                continue
            # Text inside a detected table is emitted as the table unit instead,
            # so the same content is not indexed twice.
            if any(
                layout.overlap_fraction(block.bbox, box) > config.table_overlap_frac
                for box in table_boxes
            ):
                continue

            written += _insert(
                conn,
                item,
                units.PASSAGE,
                text=text,
                page=page.page_number,
                ordinal=ordinal,
                heading=per_block.get(index),
                bbox=block.bbox,
                from_ocr=page.from_ocr,
                ocr_confidence=page.ocr_confidence,
            )
            ordinal += 1

        for table in page_tables.get(page.page_number, []):
            written += _insert(
                conn,
                item,
                units.TABLE,
                text=table.readable_text(),
                page=page.page_number,
                ordinal=ordinal,
                heading=_heading_above(page, per_block, table.bbox[1]),
                caption=table.caption,
                bbox=table.bbox,
                table_rows=table.rows_json(),
                from_ocr=page.from_ocr,
                ocr_confidence=page.ocr_confidence,
            )
            ordinal += 1

        for figure in page_figures.get(page.page_number, []):
            description = None
            if describer is not None:
                try:
                    description = describer.describe(figure.image_path, figure.caption) or None
                    if description:
                        described += 1
                except Exception as exc:  # noqa: BLE001
                    describe_errors.append(f"{type(exc).__name__}: {exc}")

            written += _insert(
                conn,
                item,
                units.FIGURE,
                text=figure.searchable_text(description),
                page=page.page_number,
                ordinal=ordinal,
                heading=_heading_above(page, per_block, figure.bbox[1]),
                caption=figure.caption,
                image_path=figure.image_path,
                image_description=description,
                bbox=figure.bbox,
                from_ocr=page.from_ocr,
                ocr_confidence=page.ocr_confidence,
            )
            ordinal += 1

    if describer is not None:
        total_figures = sum(len(v) for v in page_figures.values())
        if describe_errors:
            ledger.record(
                conn,
                item.file_id,
                ledger.STEP_DESCRIBE,
                "failed",
                f"{described}/{total_figures} described; errors: "
                + "; ".join(describe_errors[:5]),
            )
        else:
            ledger.record_outcome(
                conn,
                item.file_id,
                ledger.STEP_DESCRIBE,
                recovered_count=described,
                detail=f"{described}/{total_figures} figure(s) described "
                f"with {config.describe_model}",
            )

    return written


def _heading_above(
    page: layout.PageText, per_block: dict[int, str | None], top: float
) -> str | None:
    """The heading in force at a given height on the page.

    Tables and figures are floats: they are not in the block sequence, so they
    inherit the heading of the last text block that starts above them, rather
    than the page's first heading.
    """
    heading = None
    for index, block in enumerate(page.blocks):
        if block.top > top:
            break
        heading = per_block.get(index, heading)
    return heading


def _insert(conn: sqlite3.Connection, item: InventoryItem, unit_type: str, **kwargs) -> int:
    """Insert one unit, stamping it with its file's identity and metadata."""
    unit = units.Unit(
        file_id=item.file_id,
        source_path=item.logical_path,
        unit_type=unit_type,
        publisher=item.source.publisher,
        doc_date=item.source.date,
        text=kwargs.pop("text"),
        page=kwargs.pop("page"),
        page_ordinal=kwargs.pop("ordinal"),
        **kwargs,
    )
    units.insert(conn, unit)
    return 1


# --------------------------------------------------------------------------
# Word documents
# --------------------------------------------------------------------------


def _process_docx(
    item: InventoryItem,
    path: Path,
    config: Config,
    conn: sqlite3.Connection,
    report: RunReport,
    note: str = "",
) -> None:
    try:
        blocks = office.read_docx(path)
    except Exception as exc:  # noqa: BLE001
        ledger.record_failure(conn, item.file_id, ledger.STEP_TEXT, exc, note)
        return

    for block in blocks:
        unit = units.Unit(
            file_id=item.file_id,
            source_path=item.logical_path,
            unit_type=units.TABLE if block.kind == "table" else units.PASSAGE,
            publisher=item.source.publisher,
            doc_date=item.source.date,
            text=block.text,
            # Word documents have no fixed pagination; recorded as unknown
            # rather than guessed.
            page=None,
            page_ordinal=block.ordinal,
            heading=block.heading,
            table_rows=(
                json.dumps(
                    {"header_lines": block.header_lines or [], "rows": block.rows or []},
                    ensure_ascii=False,
                )
                if block.kind == "table"
                else None
            ),
        )
        units.insert(conn, unit)

    report.units_written += len(blocks)
    detail = (
        f"{note}{len(blocks)} block(s) from .docx; no page numbers "
        "(Office Open XML has no fixed pagination)"
    ).strip()
    ledger.record_outcome(
        conn, item.file_id, ledger.STEP_TEXT, recovered_count=len(blocks), detail=detail
    )
    ledger.record_outcome(
        conn,
        item.file_id,
        ledger.STEP_INDEX,
        recovered_count=len(blocks),
        detail=f"{len(blocks)} unit(s) indexed",
    )


def _process_doc(
    item: InventoryItem, config: Config, conn: sqlite3.Connection, report: RunReport
) -> None:
    """Legacy ``.doc``: convert with LibreOffice, or record it as source-dependent."""
    try:
        converted = office.convert_doc(item.stored_path, config)
    except office.ConversionUnavailable as exc:
        ledger.record_source_dependent(conn, item.file_id, ledger.STEP_OFFICE, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        ledger.record_failure(conn, item.file_id, ledger.STEP_OFFICE, exc)
        return

    ledger.record(
        conn,
        item.file_id,
        ledger.STEP_OFFICE,
        "recovered",
        f"converted to {converted.name} with {config.soffice_cmd}",
    )
    _process_docx(item, converted, config, conn, report, note="via LibreOffice conversion: ")


def make_describer(config: Config) -> Describer | None:
    """Build a describer only when the flag is on. Never called otherwise."""
    if not config.describe_images:
        return None
    return Describer(config)


__all__ = ["RunReport", "run", "make_describer", "DescriptionUnavailable"]
