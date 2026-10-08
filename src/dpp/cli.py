"""The command line interface. Exactly seven commands, no more:

``build``    run the full pipeline over an input directory
``refresh``  reprocess only new or changed files, by content hash
``search``   ranked results with source, page and snippet
``read``     the full text of one unit with its provenance
``context``  one unit plus its neighbours on the same page
``visual``   the preserved original image for a figure, and its caption
``report``   counts by status, and every failed or source-dependent item
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from . import db, pipeline, report as report_mod, search as search_mod, sources
from .config import Config
from .describe import DescriptionUnavailable

DEFAULT_WORK_DIR = Path("work")


def _force_utf8() -> None:
    """Make sure units survive the console.

    A Windows console defaults to a legacy code page, where printing ``°C`` or
    ``µg/L`` raises ``UnicodeEncodeError``. The project's whole premise is that
    units reach the reader exactly as written, so the streams are reconfigured
    rather than the text being sanitised.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - exotic streams
                pass


def _config(args: argparse.Namespace) -> Config:
    return Config(
        work_dir=args.work_dir,
        sources_file=getattr(args, "sources", None),
        sparse_min_chars=getattr(args, "sparse_min_chars", Config.sparse_min_chars),
        ocr_dpi=getattr(args, "ocr_dpi", Config.ocr_dpi),
        ocr_lang=getattr(args, "ocr_lang", Config.ocr_lang),
        image_min_px=getattr(args, "image_min_px", Config.image_min_px),
        image_min_area_frac=getattr(args, "image_min_area_frac", Config.image_min_area_frac),
        image_max_area_frac=getattr(args, "image_max_area_frac", Config.image_max_area_frac),
        describe_images=getattr(args, "describe_images", False),
        describe_model=getattr(args, "describe_model", Config.describe_model),
        max_pages=getattr(args, "max_pages", None),
    ).resolved()


def _connect(config: Config) -> sqlite3.Connection:
    if not db.fts5_available():
        raise SystemExit(
            "this Python's SQLite was built without FTS5; the index cannot be created"
        )
    return db.connect(config.db_path)


def _show(value: str | None) -> str:
    return sources.display(value)


def _page(value: int | None) -> str:
    return "n/a" if value is None else str(value)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    return _run_pipeline(args, only_changed=False)


def cmd_refresh(args: argparse.Namespace) -> int:
    return _run_pipeline(args, only_changed=True)


def _run_pipeline(args: argparse.Namespace, *, only_changed: bool) -> int:
    config = _config(args)
    conn = _connect(config)

    try:
        describer = pipeline.make_describer(config)
    except DescriptionUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"input      : {args.input_dir}")
    print(f"work dir   : {config.work_dir}")
    print(f"database   : {config.db_path}")
    print(f"tesseract  : {_show(config.tesseract_cmd)}")
    print(f"libreoffice: {_show(config.soffice_cmd)}")
    print(
        "describe   : "
        + (f"ON ({config.describe_model})" if describer else "OFF (no API calls, no cost)")
    )
    print()

    result = pipeline.run(
        args.input_dir, config, conn, only_changed=only_changed, describer=describer
    )
    print(result.line())
    for note in result.notes:
        print(f"  ! {note}")
    print("\nRun 'python -m dpp report' for status counts and unresolved items.")
    conn.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = _config(args)
    conn = _connect(config)
    hits = search_mod.search(conn, args.query, limit=args.limit, unit_type=args.type)

    if not hits:
        print(f"no results for {args.query!r}")
        conn.close()
        return 0

    print(f"{len(hits)} result(s) for {args.query!r}\n")
    for position, hit in enumerate(hits, start=1):
        print(f"{position}. [{hit.unit_type}] {hit.unit_id}   (bm25 {hit.rank:.2f})")
        print(f"   source   : {hit.source_path}  page {_page(hit.page)}")
        print(f"   publisher: {_show(hit.publisher)}   date: {_show(hit.doc_date)}")
        if hit.heading:
            print(f"   heading  : {hit.heading.replace(chr(10), ' / ')}")
        print(f"   snippet  : {hit.snippet}")
        print()
    conn.close()
    return 0


def _print_provenance(row: sqlite3.Row) -> None:
    print(f"source    : {row['source_path']}")
    if row["parent_path"]:
        print(f"inside    : {row['parent_path']}")
    print(f"publisher : {_show(row['publisher'])}")
    print(f"date      : {_show(row['doc_date'])}")
    print(f"page      : {_page(row['page'])}")
    print(f"heading   : {_show(row['heading'])}")
    if row["from_ocr"]:
        confidence = row["ocr_confidence"]
        # `recovered` means text was recovered, not that it is accurate; the
        # confidence belongs next to the text, not buried in the ledger.
        note = (
            f"  (text recovered by OCR, mean confidence {confidence:.1f}%)"
            if confidence is not None
            else "  (text recovered by OCR)"
        )
    else:
        note = ""
    print(f"unit type : {row['unit_type']}{note}")
    print(f"source url: {_show(row['source_url'])}")
    print(f"license   : {_show(row['license'])}")
    print(f"sha-256   : {row['sha256']}")


def cmd_read(args: argparse.Namespace) -> int:
    config = _config(args)
    conn = _connect(config)
    row = search_mod.get_unit(conn, args.unit_id)
    if row is None:
        print(f"no such unit: {args.unit_id}", file=sys.stderr)
        conn.close()
        return 1

    print(f"unit      : {row['unit_id']}")
    _print_provenance(row)
    if row["caption"]:
        print(f"caption   : {row['caption']}")
    if row["image_path"]:
        print(f"image     : {row['image_path']}")
    print()
    print(row["text"])
    if row["image_description"]:
        print(f"\n--- description (generated by {config.describe_model}, not from the document) ---")
        print(row["image_description"])
    conn.close()
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    config = _config(args)
    conn = _connect(config)
    row = search_mod.get_unit(conn, args.unit_id)
    if row is None:
        print(f"no such unit: {args.unit_id}", file=sys.stderr)
        conn.close()
        return 1

    rows = search_mod.neighbours(conn, row)
    where = "page " + _page(row["page"]) if row["page"] else "the document (no pagination)"
    print(f"{len(rows)} unit(s) on {where} of {row['source_path']}")
    print(f"publisher : {_show(row['publisher'])}   date: {_show(row['doc_date'])}\n")

    for neighbour in rows:
        marker = ">>" if neighbour["unit_id"] == row["unit_id"] else "  "
        print(f"{marker} {neighbour['unit_id']}  [{neighbour['unit_type']}]")
        if neighbour["heading"]:
            print(f"     heading: {neighbour['heading'].replace(chr(10), ' / ')}")
        body = neighbour["text"].strip()
        if neighbour["unit_id"] != row["unit_id"] and len(body) > args.excerpt:
            body = body[: args.excerpt].rstrip() + " …"
        for line in body.splitlines():
            print(f"     {line}")
        print()
    conn.close()
    return 0


def cmd_visual(args: argparse.Namespace) -> int:
    config = _config(args)
    conn = _connect(config)
    row = search_mod.get_unit(conn, args.unit_id)
    if row is None:
        print(f"no such unit: {args.unit_id}", file=sys.stderr)
        conn.close()
        return 1
    if not row["image_path"]:
        print(
            f"{args.unit_id} is a '{row['unit_type']}' unit and has no preserved image; "
            "'visual' applies to figure units",
            file=sys.stderr,
        )
        conn.close()
        return 1

    path = Path(row["image_path"])
    print(f"unit      : {row['unit_id']}")
    print(f"image     : {path}")
    print(f"exists    : {'yes' if path.is_file() else 'NO - file missing from the image store'}")
    if path.is_file():
        print(f"size      : {path.stat().st_size} bytes")
    _print_provenance(row)
    print(f"caption   : {_show(row['caption'])}")
    if row["image_description"]:
        print(f"\n--- description (generated by {config.describe_model}, not from the document) ---")
        print(row["image_description"])
    else:
        print("\nno description (run build with --describe-images to generate one)")
    conn.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = _config(args)
    conn = _connect(config)

    summary = report_mod.corpus_summary(conn)
    print("corpus")
    print(
        f"  {summary['files']} files ({summary['archive_members']} inside archives) "
        f"from {summary['publishers']} publisher(s)"
    )
    print(
        f"  {summary['units']} units: {summary['passages']} passages, "
        f"{summary['tables']} tables, {summary['figures']} figures"
    )
    print(
        f"  {summary['ocr_units']} unit(s) from OCR'd pages, "
        f"{summary['described_figures']} figure(s) with a generated description"
    )

    print("\nledger status counts")
    counts = report_mod.status_counts(conn)
    for status, count in counts.items():
        print(f"  {status:<17} {count}")

    print("\nby step")
    for row in report_mod.step_matrix(conn):
        print(f"  {row['step']:<18} {row['status']:<17} {row['n']}")

    for status, title in (
        ("failed", "failed"),
        ("source_dependent", "source-dependent (needs export or replacement)"),
    ):
        rows = report_mod.rows_with_status(conn, status)
        print(f"\n{title}: {len(rows)}")
        for row in rows:
            print(f"  {row['logical_path']}  [{row['step']}]")
            print(f"    {row['detail']}")

    if args.skipped:
        rows = report_mod.rows_with_status(conn, "skipped_by_rule")
        print(f"\nskipped by rule: {len(rows)}")
        for row in rows:
            print(f"  {row['logical_path']}  [{row['step']}]")
            print(f"    {row['detail']}")

    if args.files:
        print("\nper file")
        for row in report_mod.per_file(conn):
            print(
                f"  {row['logical_path']}\n"
                f"    {row['media_type']:<5} {row['size_bytes']:>9}B  "
                f"units={row['units']:<5} tables={row['tables'] or 0:<4} "
                f"figures={row['figures'] or 0:<4} ocr_units={row['ocr_units'] or 0}\n"
                f"    publisher={_show(row['publisher'])}  date={_show(row['doc_date'])}"
            )

    conn.close()
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dpp",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="where the database, image store and extracted files live (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("build", cmd_build, "run the full pipeline over an input directory"),
        ("refresh", cmd_refresh, "reprocess only new or changed files, by content hash"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("input_dir", type=Path, help="folder of documents to process")
        command.add_argument(
            "--sources",
            type=Path,
            default=None,
            help="provenance sidecar (default: <input_dir>/SOURCES.md)",
        )
        command.add_argument(
            "--sparse-min-chars",
            type=int,
            default=Config.sparse_min_chars,
            help="a page with fewer extractable characters is OCR'd (default: %(default)s)",
        )
        command.add_argument("--ocr-dpi", type=int, default=Config.ocr_dpi)
        command.add_argument("--ocr-lang", default=Config.ocr_lang)
        command.add_argument(
            "--image-min-px",
            type=int,
            default=Config.image_min_px,
            help="skip images smaller than this in either dimension (default: %(default)s)",
        )
        command.add_argument(
            "--image-min-area-frac",
            type=float,
            default=Config.image_min_area_frac,
            help="skip images covering less of the page than this (default: %(default)s)",
        )
        command.add_argument(
            "--image-max-area-frac",
            type=float,
            default=Config.image_max_area_frac,
            help="skip images covering more of the page than this — a page scan "
            "rather than a figure (default: %(default)s)",
        )
        command.add_argument(
            "--max-pages", type=int, default=None, help="cap pages per document (default: no cap)"
        )
        command.add_argument(
            "--describe-images",
            action="store_true",
            help="describe figures with the Anthropic API (needs ANTHROPIC_API_KEY). "
            "OFF by default: without it the pipeline makes no API calls.",
        )
        command.add_argument("--describe-model", default=Config.describe_model)
        command.set_defaults(func=handler)

    search = sub.add_parser("search", help="ranked results with source, page and snippet")
    search.add_argument("query", help="words to look for; quote a phrase to require it")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--type", choices=("passage", "table", "figure"), default=None)
    search.set_defaults(func=cmd_search)

    read = sub.add_parser("read", help="full text of one unit with its provenance")
    read.add_argument("unit_id")
    read.set_defaults(func=cmd_read)

    context = sub.add_parser("context", help="one unit plus its neighbours on the same page")
    context.add_argument("unit_id")
    context.add_argument(
        "--excerpt", type=int, default=400, help="characters to show per neighbour"
    )
    context.set_defaults(func=cmd_context)

    visual = sub.add_parser("visual", help="preserved original image for a figure, and its caption")
    visual.add_argument("unit_id")
    visual.set_defaults(func=cmd_visual)

    report = sub.add_parser("report", help="counts by status, failed and source-dependent items")
    report.add_argument("--files", action="store_true", help="also list per-file counts")
    report.add_argument("--skipped", action="store_true", help="also list skipped-by-rule items")
    report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
