"""Stage 1 — inventory.

Walks the input directory, descends into ``.zip`` archives, and records for every
file: a stable identity, its SHA-256, its detected type and its size. Archive
members keep a reference to the archive they came from.

Two different ideas of identity are kept apart on purpose:

``file_id``  derived from the *logical path*, so a file keeps its identity (and
             its ledger history) across runs even when its content changes.
``sha256``   derived from the *content*, so ``refresh`` can tell what changed.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import ledger
from .config import Config
from .sources import EMPTY, SourceRecord

#: Separator between an archive and a path inside it.
ARCHIVE_SEP = "!/"

_READ_CHUNK = 1 << 20

# Magic numbers, checked before extensions: a mislabelled file should be
# processed for what it is, not what it claims to be.
_MAGIC_PDF = b"%PDF"
_MAGIC_ZIP = b"PK\x03\x04"
_MAGIC_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

PDF = "pdf"
DOCX = "docx"
DOC = "doc"
ZIP = "zip"
OTHER = "other"


@dataclass(frozen=True)
class InventoryItem:
    """One inventoried file, before any content extraction."""

    file_id: str
    logical_path: str
    stored_path: Path
    sha256: str
    size_bytes: int
    media_type: str
    parent_archive_id: str | None
    source: SourceRecord = EMPTY

    @property
    def display_name(self) -> str:
        return self.logical_path


def make_file_id(logical_path: str) -> str:
    """Stable identity for a logical path. Independent of content."""
    return hashlib.sha256(logical_path.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks so large PDFs do not land in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_media_type(path: Path) -> str:
    """Classify by magic bytes, falling back to the extension."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        head = b""

    suffix = path.suffix.lower()

    if head.startswith(_MAGIC_PDF):
        return PDF
    if head.startswith(_MAGIC_ZIP):
        # .docx is a zip; distinguish by looking for the Word part rather than
        # trusting the name.
        if _looks_like_docx(path):
            return DOCX
        return ZIP
    if head.startswith(_MAGIC_OLE2):
        # OLE2 also covers legacy .xls/.ppt; only .doc is in scope here.
        return DOC if suffix == ".doc" else OTHER
    if suffix == ".pdf":
        return PDF
    if suffix == ".docx":
        return DOCX
    if suffix == ".doc":
        return DOC
    if suffix == ".zip":
        return ZIP
    return OTHER


def _looks_like_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return "word/document.xml" in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _safe_member_name(name: str) -> str | None:
    """Reject archive members that would escape the extraction directory."""
    if name.endswith("/"):
        return None
    normalised = name.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if Path(normalised).is_absolute() or (len(parts[0]) == 2 and parts[0][1] == ":"):
        return None
    return "/".join(parts)


def walk(
    input_dir: Path,
    config: Config,
    metadata: dict[str, SourceRecord] | None = None,
    *,
    skip_names: frozenset[str] = frozenset({"SOURCES.md"}),
) -> list[InventoryItem]:
    """Inventory ``input_dir`` recursively, descending into archives.

    Returns items in a deterministic order: an archive immediately precedes its
    own members, so a parent is always inserted before the rows referencing it.
    """
    metadata = metadata or {}
    items: list[InventoryItem] = []

    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if path.name in skip_names:
            continue
        logical = path.relative_to(input_dir).as_posix()
        item = _describe(path, logical, None, metadata)
        items.append(item)
        if item.media_type == ZIP:
            items.extend(_walk_archive(item, config, metadata, depth=1))

    return items


def _describe(
    path: Path,
    logical_path: str,
    parent_archive_id: str | None,
    metadata: dict[str, SourceRecord],
) -> InventoryItem:
    return InventoryItem(
        file_id=make_file_id(logical_path),
        logical_path=logical_path,
        stored_path=path,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        media_type=detect_media_type(path),
        parent_archive_id=parent_archive_id,
        source=metadata.get(logical_path, EMPTY),
    )


def _walk_archive(
    archive_item: InventoryItem,
    config: Config,
    metadata: dict[str, SourceRecord],
    depth: int,
) -> list[InventoryItem]:
    """Expand one archive into inventory items for its members."""
    if depth > config.archive_max_depth:
        return []

    target = config.extracted_dir / archive_item.file_id
    items: list[InventoryItem] = []

    with zipfile.ZipFile(archive_item.stored_path) as archive:
        for name in sorted(archive.namelist()):
            safe = _safe_member_name(name)
            if safe is None:
                continue
            destination = target / safe
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, open(destination, "wb") as dst:
                while chunk := src.read(_READ_CHUNK):
                    dst.write(chunk)

            logical = f"{archive_item.logical_path}{ARCHIVE_SEP}{safe}"
            member = _describe(destination, logical, archive_item.file_id, metadata)
            items.append(member)
            if member.media_type == ZIP:
                items.extend(_walk_archive(member, config, metadata, depth + 1))

    return items


def upsert(conn: sqlite3.Connection, item: InventoryItem) -> str | None:
    """Insert or update the ``files`` row. Returns the previous SHA-256, if any.

    A returned hash equal to ``item.sha256`` means the file is unchanged, which
    is what ``refresh`` uses to skip work.
    """
    existing = conn.execute(
        "SELECT sha256 FROM files WHERE file_id = ?", (item.file_id,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if existing is None:
        conn.execute(
            """INSERT INTO files (file_id, logical_path, stored_path, sha256, size_bytes,
                                  media_type, parent_archive_id, title, publisher, doc_date,
                                  source_url, license, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.file_id,
                item.logical_path,
                str(item.stored_path),
                item.sha256,
                item.size_bytes,
                item.media_type,
                item.parent_archive_id,
                item.source.title,
                item.source.publisher,
                item.source.date,
                item.source.source_url,
                item.source.license,
                now,
            ),
        )
        return None

    conn.execute(
        """UPDATE files
              SET stored_path = ?, sha256 = ?, size_bytes = ?, media_type = ?,
                  parent_archive_id = ?, title = ?, publisher = ?, doc_date = ?,
                  source_url = ?, license = ?
            WHERE file_id = ?""",
        (
            str(item.stored_path),
            item.sha256,
            item.size_bytes,
            item.media_type,
            item.parent_archive_id,
            item.source.title,
            item.source.publisher,
            item.source.date,
            item.source.source_url,
            item.source.license,
            item.file_id,
        ),
    )
    return existing["sha256"]


def record_inventory_step(conn: sqlite3.Connection, item: InventoryItem) -> None:
    """Ledger the inventory step for one file."""
    detail = (
        f"sha256={item.sha256} size={item.size_bytes}B type={item.media_type}"
        + (f" parent={item.parent_archive_id}" if item.parent_archive_id else "")
    )
    if item.media_type == OTHER:
        ledger.record_skipped(
            conn,
            item.file_id,
            ledger.STEP_INVENTORY,
            f"unsupported media type for extraction (detected '{item.media_type}'); {detail}",
        )
    else:
        ledger.record(conn, item.file_id, ledger.STEP_INVENTORY, "recovered", detail)
