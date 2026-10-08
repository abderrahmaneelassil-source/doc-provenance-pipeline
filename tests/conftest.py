"""Shared fixtures. Tests build their own fixture documents, so the suite runs
without the sample corpus and without network access."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import fitz
import pytest

from dpp import db
from dpp.config import Config


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """A Config rooted in a temporary directory — nothing machine-specific."""
    cfg = Config(work_dir=tmp_path / "work").resolved()
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def conn(config: Config) -> sqlite3.Connection:
    connection = db.connect(config.db_path)
    yield connection
    connection.close()


@pytest.fixture()
def register_file(conn: sqlite3.Connection):
    """Insert a minimal ``files`` row so ledger/unit foreign keys resolve."""

    def _register(file_id: str = "f0000000deadbeef", **overrides) -> str:
        values = {
            "file_id": file_id,
            "logical_path": overrides.get("logical_path", f"{file_id}.pdf"),
            "stored_path": overrides.get("stored_path", f"/tmp/{file_id}.pdf"),
            "sha256": overrides.get("sha256", "0" * 64),
            "size_bytes": overrides.get("size_bytes", 1),
            "media_type": overrides.get("media_type", "pdf"),
            "publisher": overrides.get("publisher", "U.S. Geological Survey"),
            "doc_date": overrides.get("doc_date", "2016-07-12"),
            "first_seen": "2026-01-01T00:00:00+00:00",
        }
        conn.execute(
            """INSERT INTO files (file_id, logical_path, stored_path, sha256, size_bytes,
                                  media_type, publisher, doc_date, first_seen)
               VALUES (:file_id, :logical_path, :stored_path, :sha256, :size_bytes,
                       :media_type, :publisher, :doc_date, :first_seen)""",
            values,
        )
        conn.commit()
        return file_id

    return _register


def make_pdf(path: Path, pages: list[str], fontsize: int = 11) -> Path:
    """Write a small text PDF. Empty strings produce genuinely blank pages."""
    doc = fitz.open()
    try:
        for body in pages:
            page = doc.new_page(width=612, height=792)
            if body:
                page.insert_textbox(
                    fitz.Rect(56, 56, 556, 736), body, fontsize=fontsize, fontname="helv"
                )
        doc.save(path)
    finally:
        doc.close()
    return path
