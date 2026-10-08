"""Figure extraction: placements, deduplication, the skip rule, and context.

What a figure unit is for: the visual itself cannot be searched, so the unit
carries the words that surround it — its caption and the nearby text — while the
original image file is preserved on disk so ``visual`` can hand back the picture
a reader actually wants to see.

Three decisions are worth stating:

* **Deduplication is per file, by content hash.** A logo repeated on 90 pages
  produces one unit, not 90. Deduplication deliberately does *not* span files:
  the same chart in two reports is two findings with two provenances, and
  collapsing them would merge sources.
* **The image bytes are stored once**, content-addressed by that same hash, so
  per-file units can share a path without duplicating megabytes on disk.
* **The skip rule is explicit and configurable**, and every skip is counted in
  the ledger as ``skipped_by_rule`` naming the threshold that fired.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from ..config import Config
from .layout import BBox, Line, PageText, extend_caption

#: Caption conventions for floats across the sample set.
_FIGURE_CAPTION = re.compile(
    r"^\s*(figure|fig\.|plate|chart|exhibit|photograph|photo)\s*[\dIVXLC]+\s*[.\-–—:]?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedImage:
    """One image placement, with its provenance and the words around it."""

    page_number: int
    bbox: BBox
    image_sha256: str
    image_path: Path
    width: int
    height: int
    caption: str | None
    context: str
    repeat_count: int = 1
    """How many further identical placements in this file this unit stands for."""

    def searchable_text(self, description: str | None = None) -> str:
        """Caption, surrounding text, and a description only if one was made.

        With ``--describe-images`` off there is no description, and none is
        invented — the unit is simply the words the document itself put near the
        figure.
        """
        parts = [part for part in (self.caption, self.context, description) if part]
        return "\n\n".join(parts).strip()


@dataclass(frozen=True)
class SkipReason:
    """Why one placement was skipped, in words that name the rule."""

    rule: str
    detail: str


def skip_reason(
    bbox: BBox, width: int, height: int, page: PageText, config: Config
) -> SkipReason | None:
    """Apply the decorative-image rule. ``None`` means keep the image.

    Rules, in order: too few pixels to carry information, too small a share of
    the page to be a figure, *too large* a share to be a figure at all, or too
    elongated to be anything but a rule, border or gradient strip.
    """
    if width < config.image_min_px or height < config.image_min_px:
        return SkipReason(
            "image_min_px",
            f"{width}x{height}px below image_min_px={config.image_min_px}",
        )

    page_area = page.width * page.height
    placed_area = max(bbox[2] - bbox[0], 0) * max(bbox[3] - bbox[1], 0)
    if page_area > 0:
        fraction = placed_area / page_area
        if fraction < config.image_min_area_frac:
            return SkipReason(
                "image_min_area_frac",
                f"covers {fraction:.4f} of the page, below "
                f"image_min_area_frac={config.image_min_area_frac}",
            )
        if fraction > config.image_max_area_frac:
            # In a scanned document every page is one big image. Those are page
            # scans, not figures: the page's words are already indexed as
            # passages (from the text layer or from OCR), and emitting a figure
            # unit per page would bury the real figures under hundreds of
            # captionless duplicates. Measured on the sample set, this is 247 of
            # 256 detected images.
            return SkipReason(
                "image_max_area_frac",
                f"covers {fraction:.2f} of the page, above "
                f"image_max_area_frac={config.image_max_area_frac}: a page scan, "
                "not a figure",
            )

    short, long = sorted((width, height))
    if short > 0 and (long / short) > config.image_max_aspect:
        return SkipReason(
            "image_max_aspect",
            f"aspect {long / short:.1f}:1 exceeds image_max_aspect={config.image_max_aspect}",
        )

    return None


def is_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION.match(text))


def find_caption(page: PageText, bbox: BBox, window_pt: float) -> str | None:
    """Nearest ``Figure N ...`` caption below the figure, else above it.

    Figure captions sit below the image by convention, so below is checked
    first. Continuation lines are joined on, because these captions wrap.
    """
    below: list[tuple[float, Line]] = []
    above: list[tuple[float, Line]] = []

    for block in page.blocks:
        for line in block.lines:
            if not _FIGURE_CAPTION.match(line.text):
                continue
            if line.top >= bbox[3] and (line.top - bbox[3]) <= window_pt:
                below.append((line.top - bbox[3], line))
            elif line.bottom <= bbox[1] and (bbox[1] - line.bottom) <= window_pt:
                above.append((bbox[1] - line.bottom, line))

    for candidates in (below, above):
        if candidates:
            first = min(candidates, key=lambda item: item[0])[1]
            return extend_caption(page, first, is_figure_caption)
    return None


def surrounding_text(
    page: PageText, bbox: BBox, window_pt: float, caption: str | None = None
) -> str:
    """Text blocks within a vertical window of the figure, in reading order."""
    top = bbox[1] - window_pt
    bottom = bbox[3] + window_pt
    parts: list[str] = []

    for block in sorted(page.blocks, key=lambda b: (b.top, b.bbox[0])):
        if block.bottom < top or block.top > bottom:
            continue
        text = block.text
        if not text or text == caption:
            continue
        parts.append(text)

    return "\n\n".join(parts).strip()


def extract_page_images(
    page: fitz.Page,
    page_text: PageText,
    config: Config,
    seen: dict[str, Path],
) -> tuple[list[ExtractedImage], list[SkipReason], int]:
    """Extract the keepable image placements on one page.

    ``seen`` maps image hash to stored path for this file, and is updated in
    place; a hash already present means a repeat placement, which is counted
    rather than turned into a second unit.

    Returns the new figures, the skip reasons that fired, and the number of
    repeat placements collapsed.
    """
    figures: list[ExtractedImage] = []
    skipped: list[SkipReason] = []
    repeats = 0
    doc = page.parent

    for entry in page.get_images(full=True):
        xref = entry[0]
        try:
            rects = page.get_image_rects(xref)
        except (ValueError, RuntimeError):
            rects = []
        if not rects:
            continue

        try:
            raw = doc.extract_image(xref)
        except (ValueError, RuntimeError):
            continue
        data = raw.get("image")
        if not data:
            continue

        digest = hashlib.sha256(data).hexdigest()
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)

        for rect in rects:
            bbox: BBox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

            reason = skip_reason(bbox, width, height, page_text, config)
            if reason is not None:
                skipped.append(reason)
                continue

            if digest in seen:
                repeats += 1
                continue

            stored = _store(data, raw.get("ext", "png"), digest, config)
            seen[digest] = stored

            caption = find_caption(page_text, bbox, config.caption_window_pt)
            figures.append(
                ExtractedImage(
                    page_number=page_text.page_number,
                    bbox=bbox,
                    image_sha256=digest,
                    image_path=stored,
                    width=width,
                    height=height,
                    caption=caption,
                    context=surrounding_text(
                        page_text, bbox, config.context_window_pt, caption
                    ),
                )
            )

    return figures, skipped, repeats


def _store(data: bytes, ext: str, digest: str, config: Config) -> Path:
    """Write the original bytes to the content-addressed store, once."""
    config.images_dir.mkdir(parents=True, exist_ok=True)
    destination = config.images_dir / f"{digest}.{ext.lstrip('.') or 'png'}"
    if not destination.exists():
        destination.write_bytes(data)
    return destination
