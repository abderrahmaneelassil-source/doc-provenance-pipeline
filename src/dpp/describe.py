"""Optional vision descriptions for extracted figures.

**Off by default.** Without ``--describe-images`` this module is never called, no
API key is read, no request is made, and the pipeline costs nothing to run. The
``image_description`` text of a figure unit stays empty — a figure the pipeline
could not describe is recorded as undescribed, never filled in with a guess.

With the flag on, each kept figure is sent to the Anthropic API as a base64
image block and the returned text is stored alongside the caption, clearly
attributed to the model rather than to the document.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import Config

#: Media types the Messages API accepts for image blocks. Anything else is
#: converted to PNG before sending.
_SUPPORTED = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: Descriptions are deliberately short — they exist to make a figure findable by
#: search, not to replace looking at it.
MAX_TOKENS = 1024

PROMPT = (
    "Describe this figure from a technical document in 2-4 sentences, for "
    "someone searching a document library who cannot see it. State what kind of "
    "figure it is (map, chart, photograph, schematic) and what it shows, "
    "including any axis labels, units, or legend categories you can read. "
    "Describe only what is visible. Do not speculate about conclusions the "
    "figure might support."
)


class DescriptionUnavailable(RuntimeError):
    """Raised when descriptions were asked for but cannot be produced."""


@dataclass
class Describer:
    """Thin wrapper over the Anthropic client, constructed only when enabled."""

    config: Config
    _client: object | None = None

    def __post_init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise DescriptionUnavailable(
                "--describe-images needs the anthropic package: "
                "pip install 'doc-provenance-pipeline[describe]'"
            ) from exc

        # The SDK resolves credentials itself (ANTHROPIC_API_KEY, or a CLI
        # profile); we only check up front so the failure is reported once at
        # the start of a run instead of once per figure.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise DescriptionUnavailable(
                "--describe-images needs ANTHROPIC_API_KEY in the environment"
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    def _encode(self, path: Path) -> tuple[str, str]:
        """Return ``(media_type, base64_data)`` for one stored image."""
        media_type = _SUPPORTED.get(path.suffix.lower())
        if media_type:
            return media_type, base64.standard_b64encode(path.read_bytes()).decode()

        buffer = io.BytesIO()
        with Image.open(path) as image:
            image.convert("RGB").save(buffer, format="PNG")
        return "image/png", base64.standard_b64encode(buffer.getvalue()).decode()

    def describe(self, image_path: Path, caption: str | None = None) -> str:
        """Describe one figure. Returns ``""`` if the model declined.

        A refusal is a normal 200 response, so ``stop_reason`` is checked before
        the content is read.
        """
        media_type, data = self._encode(image_path)
        prompt = PROMPT
        if caption:
            prompt += f"\n\nThe document captions it: {caption!r}"

        message = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        response = self._request(message)
        if getattr(response, "stop_reason", None) == "refusal":
            return ""
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()

    def _request(self, messages: list[dict]):
        """Send the request, preferring server-side refusal fallbacks.

        Claude Opus 5's safety classifiers can decline a request; the
        ``fallbacks`` parameter re-runs a declined request on another model
        server-side, so a refusal on one figure does not just lose that figure.

        It is a beta, and the installed SDK may predate it. Both failure shapes
        degrade to a plain request rather than ending the run: ``TypeError`` when
        the SDK does not know the parameter, ``BadRequestError`` when the server
        rejects the beta.
        """
        try:
            return self._client.beta.messages.create(
                model=self.config.describe_model,
                max_tokens=MAX_TOKENS,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=messages,
            )
        except (TypeError, self._anthropic.BadRequestError):
            return self._client.messages.create(
                model=self.config.describe_model,
                max_tokens=MAX_TOKENS,
                messages=messages,
            )
