# doc-provenance-pipeline
#
# The pipeline's two external dependencies are the reason this image exists:
# Tesseract (OCR for sparse pages) and LibreOffice (legacy .doc conversion).
# Without them the pipeline still runs and reports honestly — sparse pages are
# recorded `source_dependent` and .doc files "needs export or replacement" —
# but nothing is recovered from either. Installed here so that never happens.
#
#   docker build -t dpp .
#   docker run --rm -v "$PWD/samples:/data/samples" -v "$PWD/work:/data/work" \
#              dpp build /data/samples
#   docker run --rm -v "$PWD/work:/data/work" dpp search "mg/L"
#
# Python dependencies are pinned exactly in pyproject.toml. The base image is
# pinned to a Debian release; the apt packages below are not version-pinned,
# because Debian removes superseded versions from the archive and a stale pin
# turns into a build failure rather than reproducibility. Pin them to the
# versions in your own apt mirror if you need bit-reproducible images.
#
# NOTE: this image has not been built in the environment where the pipeline was
# developed (no Docker available there), so treat the build itself as unverified.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # The CLI prints units verbatim (mg/L, °C, kt); make sure the locale can.
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # Where the pipeline writes its database, image store and extracted files.
    DPP_WORK_DIR=/data/work

# --- system dependencies --------------------------------------------------
# tesseract-ocr + the English model: OCR for image-only pages.
# libreoffice-writer (not the full suite): converts legacy binary .doc.
# default-jre-headless: LibreOffice's headless filters need a JRE.
# fonts-dejavu-core: without a font, rendering and conversion silently degrade.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      tesseract-ocr \
      tesseract-ocr-eng \
      libreoffice-writer \
      default-jre-headless \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# --- python dependencies --------------------------------------------------
# Copied before the source so dependency layers cache across code edits.
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# --- runtime --------------------------------------------------------------
# Run as a non-root user; /data is the only path that needs to be writable,
# and it is where volumes get mounted.
RUN useradd --create-home --uid 10001 dpp \
 && mkdir -p /data/work /data/samples \
 && chown -R dpp:dpp /data
USER dpp
WORKDIR /data

# A smoke check that both binaries resolved, so a broken image fails loudly
# at build time rather than quietly recording `source_dependent` at run time.
RUN tesseract --version && soffice --version \
 && python -c "from dpp.config import find_tesseract, find_soffice; \
assert find_tesseract(), 'tesseract not found'; \
assert find_soffice(), 'libreoffice not found'; \
print('external tools OK')" \
 && python -c "from dpp.db import fts5_available; \
assert fts5_available(), 'SQLite built without FTS5'; print('fts5 OK')"

ENTRYPOINT ["python", "-m", "dpp", "--work-dir", "/data/work"]
CMD ["report"]
