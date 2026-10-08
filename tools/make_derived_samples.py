"""Build the two *derived* sample files, reproducibly.

Everything else in ``samples/`` was downloaded verbatim from its publisher. Two
files could not be: no publisher we could find serves an image-only PDF (every
government scan we tested already carried an OCR text layer), and the sample set
needs a ``.zip`` archive to exercise archive-member inventory.

Both derivations are recorded in ``samples/SOURCES.md`` with their parent
document, so no provenance is invented.

The three parent documents are not kept in the repository (``samples/`` holds
the finished files instead). Download them into ``--downloads`` first; the URLs
are the ``source_url`` values in ``samples/SOURCES.md``::

    fs20183024.pdf   https://pubs.usgs.gov/fs/2018/3024/fs20183024.pdf
    fs20163062.pdf   https://pubs.usgs.gov/fs/2016/3062/fs20163062.pdf
    fs2013-3105.pdf  https://pubs.usgs.gov/fs/2013/3105/pdf/fs2013-3105.pdf

Usage::

    python tools/make_derived_samples.py --samples samples --downloads work/candidates
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import fitz
from PIL import Image

# Rasterising at this DPI keeps the pages legible to Tesseract while holding the
# derived file to a sane size. JPEG rather than PNG for the same reason — a real
# scan would not be lossless either.
RASTER_DPI = 200
JPEG_QUALITY = 70


def rasterise(src: Path, dest: Path, pages: int, dpi: int = RASTER_DPI) -> None:
    """Write ``pages`` pages of ``src`` as image-only pages — no text layer."""
    src_doc = fitz.open(src)
    out = fitz.open()
    try:
        for index in range(min(pages, src_doc.page_count)):
            pix = src_doc[index].get_pixmap(dpi=dpi)
            buf = io.BytesIO()
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(
                buf, format="JPEG", quality=JPEG_QUALITY, optimize=True
            )
            page = out.new_page(width=pix.width * 72 / dpi, height=pix.height * 72 / dpi)
            page.insert_image(page.rect, stream=buf.getvalue())
        out.save(dest, deflate=True, garbage=4)
    finally:
        out.close()
        src_doc.close()

    check = fitz.open(dest)
    try:
        leftover = sum(len(check[i].get_text("text").strip()) for i in range(check.page_count))
    finally:
        check.close()
    if leftover:
        raise SystemExit(f"{dest.name}: expected no text layer, found {leftover} characters")
    print(f"wrote {dest} ({dest.stat().st_size} bytes, image-only, no text layer)")


def bundle(members: list[Path], dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            zf.write(member, arcname=member.name)
    print(f"wrote {dest} ({dest.stat().st_size} bytes, {len(members)} members)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    parser.add_argument(
        "--downloads",
        type=Path,
        default=Path("work/candidates"),
        help="directory holding the verbatim downloads the derived files are built from",
    )
    args = parser.parse_args()
    args.samples.mkdir(parents=True, exist_ok=True)

    rasterise(
        args.downloads / "fs20183024.pdf",
        args.samples / "usgs_fs2018-3024_imageonly_scan_derived.pdf",
        pages=3,
    )
    bundle(
        [args.downloads / "fs20163062.pdf", args.downloads / "fs2013-3105.pdf"],
        args.samples / "usgs_factsheets_bundle.zip",
    )


if __name__ == "__main__":
    main()
