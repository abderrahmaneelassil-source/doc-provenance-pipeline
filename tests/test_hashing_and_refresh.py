"""Hashing and refresh detection.

Two identities are under test and they must not be confused: ``file_id`` is
derived from the path and must survive a content change, while ``sha256`` is
derived from the content and is what tells ``refresh`` something moved.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path

from conftest import make_pdf

from dpp import inventory
from dpp.config import Config


def test_sha256_matches_hashlib(tmp_path: Path):
    target = tmp_path / "data.bin"
    payload = b"mg/L \xc2\xb0C kt" * 1000
    target.write_bytes(payload)

    assert inventory.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_sha256_is_chunk_boundary_safe(tmp_path: Path):
    """Hashing reads in 1 MiB chunks; a file larger than one chunk must agree."""
    target = tmp_path / "big.bin"
    payload = bytes(range(256)) * ((1 << 20) // 256 + 17)
    target.write_bytes(payload)

    assert inventory.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_file_id_is_stable_for_a_path_and_independent_of_content(tmp_path: Path):
    first = inventory.make_file_id("report.pdf")
    assert first == inventory.make_file_id("report.pdf")
    assert first != inventory.make_file_id("other.pdf")
    # Archive members get their own identity, distinct from the archive.
    assert first != inventory.make_file_id("report.pdf!/inner.pdf")


def test_unchanged_file_reports_the_same_hash_so_refresh_can_skip(
    tmp_path: Path, conn: sqlite3.Connection, config: Config
):
    samples = tmp_path / "samples"
    samples.mkdir()
    make_pdf(samples / "a.pdf", ["Dissolved solids reported in mg/L."])

    first = inventory.walk(samples, config)
    assert inventory.upsert(conn, first[0]) is None  # new file, no previous hash

    second = inventory.walk(samples, config)
    previous = inventory.upsert(conn, second[0])
    assert previous == second[0].sha256, "an untouched file must look unchanged"


def test_changed_content_is_detected_while_identity_is_kept(
    tmp_path: Path, conn: sqlite3.Connection, config: Config
):
    samples = tmp_path / "samples"
    samples.mkdir()
    path = samples / "a.pdf"
    make_pdf(path, ["Version one."])

    original = inventory.walk(samples, config)[0]
    inventory.upsert(conn, original)

    make_pdf(path, ["Version two, substantially rewritten."])
    updated = inventory.walk(samples, config)[0]
    previous = inventory.upsert(conn, updated)

    assert previous == original.sha256
    assert updated.sha256 != original.sha256, "refresh must see the content change"
    assert updated.file_id == original.file_id, "identity follows the path, not the bytes"


def test_archive_members_are_inventoried_with_a_parent_reference(
    tmp_path: Path, config: Config
):
    samples = tmp_path / "samples"
    samples.mkdir()
    inner = tmp_path / "inner.pdf"
    make_pdf(inner, ["Calcium concentrations in mg/L."])

    archive = samples / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(inner, arcname="inner.pdf")

    items = inventory.walk(samples, config)
    by_path = {item.logical_path: item for item in items}

    assert "bundle.zip" in by_path
    member = by_path["bundle.zip!/inner.pdf"]
    assert member.parent_archive_id == by_path["bundle.zip"].file_id
    assert member.media_type == inventory.PDF
    assert member.sha256 == inventory.sha256_file(inner)


def test_media_type_comes_from_magic_bytes_not_the_extension(tmp_path: Path):
    pdf = make_pdf(tmp_path / "actually_a_pdf.txt", ["Body text."])
    assert inventory.detect_media_type(pdf) == inventory.PDF

    ole2 = tmp_path / "legacy.doc"
    ole2.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    assert inventory.detect_media_type(ole2) == inventory.DOC


def test_zip_slip_members_are_refused(tmp_path: Path, config: Config):
    """An archive entry that would escape the extraction directory is dropped."""
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
        zf.writestr("fine.txt", "ok")

    items = inventory.walk(samples, config)
    member_paths = [i.logical_path for i in items if i.parent_archive_id]
    assert member_paths == ["evil.zip!/fine.txt"]
