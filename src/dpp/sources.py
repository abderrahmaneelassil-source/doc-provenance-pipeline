"""Reader for the ``SOURCES.md`` provenance sidecar.

``SOURCES.md`` is the single source of truth for publisher, date, URL and licence.
It is written to be read by a person *and* parsed here, so the documentation and
the metadata on each unit cannot drift apart.

Format — a level-2 heading naming the file, then ``- key: value`` lines::

    ## report.pdf
    - title: Some report
    - publisher: U.S. Geological Survey
    - date: 2016-07-12
    - source_url: https://example.gov/report.pdf
    - license: Public domain
    - notes: free text

Archive members are addressed ``archive.zip!/member.pdf``.

Nothing is inferred. A file with no entry, or a key the sidecar leaves out, gets
``None`` here and is stored and displayed as ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

UNKNOWN = "unknown"

_HEADING = re.compile(r"^##\s+(?P<path>\S.*?)\s*$")
_FIELD = re.compile(r"^\s*-\s*(?P<key>[a-z_]+)\s*:\s*(?P<value>.*?)\s*$")

_KEYS = ("title", "publisher", "date", "source_url", "license", "notes")


@dataclass(frozen=True)
class SourceRecord:
    """Publisher-stated metadata for one file. ``None`` means not stated."""

    title: str | None = None
    publisher: str | None = None
    date: str | None = None
    source_url: str | None = None
    license: str | None = None
    notes: str | None = None


EMPTY = SourceRecord()


def _normalise(value: str) -> str | None:
    """Treat an explicit ``unknown`` in the sidecar the same as an absent key."""
    stripped = value.strip()
    if not stripped or stripped.lower() == UNKNOWN:
        return None
    return stripped


def parse(text: str) -> dict[str, SourceRecord]:
    """Parse sidecar text into ``{logical_path: SourceRecord}``."""
    entries: dict[str, dict[str, str | None]] = {}
    current: dict[str, str | None] | None = None

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = {}
            entries[heading.group("path")] = current
            continue
        if current is None:
            continue
        field = _FIELD.match(line)
        if field and field.group("key") in _KEYS:
            current[field.group("key")] = _normalise(field.group("value"))

    return {path: SourceRecord(**fields) for path, fields in entries.items()}


def load(path: Path | None) -> dict[str, SourceRecord]:
    """Load a sidecar, returning an empty mapping when there is none."""
    if path is None or not path.is_file():
        return {}
    return parse(path.read_text(encoding="utf-8"))


def display(value: str | None) -> str:
    """Render a possibly-absent metadata value for output."""
    return value if value else UNKNOWN
