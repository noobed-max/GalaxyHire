"""Capability and bounded document-media helpers for LLM ingestion.

This module deliberately keeps provider/model capability decisions separate from
the transport implementation.  An unknown model is treated as text-only: an
OpenAI-compatible endpoint is not evidence that it accepts image or file
parts.  PDF page images are rendered locally and carried in memory only; no
uploaded file is persisted by this layer.
"""

from __future__ import annotations

import base64
import enum
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CapabilityState(enum.StrEnum):
    """A deliberately explicit capability result (never an implicit bool)."""

    SUPPORTED = "supported"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ModelCapabilities:
    image_input: CapabilityState
    pdf_input: CapabilityState
    endpoint: str
    source: str

    @property
    def media_supported(self) -> bool:
        return self.image_input is CapabilityState.SUPPORTED


@dataclass(frozen=True)
class MediaImage:
    """A bounded PNG page, represented as raw base64 for API request builders."""

    data: str
    media_type: str = "image/png"
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class PdfMedia:
    images: tuple[MediaImage, ...]
    pages_rendered: int
    pages_available: int | None = None


# These bounds are intentionally conservative for a résumé.  They protect
# memory, request size and provider image-count limits without affecting the
# text extraction path.
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES_FOR_MEDIA = 8
MAX_RENDERED_PIXELS = 3_200_000
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BASE64_BYTES = 14 * 1024 * 1024
RENDER_DPI = 144
RENDER_SCALE = 1800


def _model_matches(model: str, *prefixes: str) -> bool:
    value = (model or "").strip().lower()
    return any(value == prefix or value.startswith(prefix + "-") for prefix in prefixes)


def model_capabilities(provider: str, model: str, endpoint: str = "chat/completions") -> ModelCapabilities:
    """Resolve media support without over-claiming compatibility.

    The OpenAI model catalog explicitly describes current GPT-4o/GPT-4.1/GPT-5
    families as accepting image input. Anthropic's current Claude 3+ API docs
    document image blocks. OpenCode Zen documents the endpoint for
    ``muse-spark-1.3-contributor`` but not its input modalities, so that model
    remains ``unknown`` and safely uses text extraction.
    """

    provider_key = (provider or "").strip().lower()
    model_key = (model or "").strip().lower()
    endpoint_key = (endpoint or "").strip().lower()
    source = "conservative-default"

    if provider_key == "openai":
        if _model_matches(model_key, "gpt-4o", "gpt-4.1", "gpt-5", "gpt-5.1", "gpt-5.2", "gpt-5.5", "gpt-5.6", "gpt-6"):
            source = "openai-model-catalog"
            return ModelCapabilities(CapabilityState.SUPPORTED, CapabilityState.SUPPORTED, endpoint_key, source)
        if model_key.startswith(("o1", "o3", "o4")):
            source = "openai-model-catalog"
            return ModelCapabilities(CapabilityState.SUPPORTED, CapabilityState.SUPPORTED, endpoint_key, source)
        if model_key in {"gpt-4", "gpt-3.5-turbo", "text-davinci-003"}:
            source = "openai-model-catalog"
            return ModelCapabilities(CapabilityState.UNSUPPORTED, CapabilityState.UNSUPPORTED, endpoint_key, source)
        return ModelCapabilities(CapabilityState.UNKNOWN, CapabilityState.UNKNOWN, endpoint_key, source)

    if provider_key == "anthropic":
        if model_key.startswith(("claude-3", "claude-4", "claude-sonnet", "claude-opus", "claude-haiku")):
            return ModelCapabilities(CapabilityState.SUPPORTED, CapabilityState.UNKNOWN, endpoint_key, "anthropic-vision-docs")
        return ModelCapabilities(CapabilityState.UNKNOWN, CapabilityState.UNKNOWN, endpoint_key, source)

    # Gemini's OpenAI-compatible endpoint is known to support images for the
    # Gemini model family. Other compatibility providers stay unknown because
    # endpoint compatibility does not imply model media support.
    if provider_key == "gemini" and model_key.startswith("gemini"):
        return ModelCapabilities(CapabilityState.SUPPORTED, CapabilityState.UNKNOWN, endpoint_key, "google-gemini-model-family")

    # OpenCode's public Zen page advertises endpoint/provider mappings but does
    # not publish modality metadata for muse-spark contributor models.
    if provider_key == "opencode":
        return ModelCapabilities(CapabilityState.UNKNOWN, CapabilityState.UNKNOWN, endpoint_key, "opencode-model-metadata-unknown")

    # Custom endpoints and all other OpenAI-compatible providers are unknown by
    # default. They may be upgraded later from verified model metadata or an
    # explicit user setting, but must never receive media speculatively.
    return ModelCapabilities(CapabilityState.UNKNOWN, CapabilityState.UNKNOWN, endpoint_key, source)


def capability_for(provider: str, model: str, endpoint: str = "chat/completions") -> ModelCapabilities:
    """Public alias used by callers and tests."""

    return model_capabilities(provider, model, endpoint)


def _png_size(data: bytes) -> tuple[int, int]:
    # PNG signature + IHDR width/height.  Avoid a heavyweight image dependency.
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("renderer returned a non-PNG image")
    return struct.unpack(">II", data[16:24])


def _page_count(path: str) -> int | None:
    try:
        result = subprocess.run(
            ["pdfinfo", "--", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.lower().startswith("pages:"):
            try:
                return max(0, int(line.split(":", 1)[1].strip()))
            except ValueError:
                return None
    return None


def render_pdf_media(path: str, *, max_pages: int = MAX_PDF_PAGES_FOR_MEDIA) -> PdfMedia:
    """Render at most ``max_pages`` of a PDF into bounded PNG page images.

    ``pdftoppm`` is invoked with an argument list and a temporary output
    directory.  The input bytes and page contents are never included in an
    exception or log message. The caller decides whether model capability is
    supported before invoking this function.
    """

    source = Path(path)
    if not source.is_file():
        raise ValueError("document is not available for visual ingestion")
    size = source.stat().st_size
    if size <= 0 or size > MAX_PDF_BYTES:
        raise ValueError("document exceeds the visual-ingestion size limit")
    page_count = _page_count(str(source))
    upper = max(1, min(int(max_pages), MAX_PDF_PAGES_FOR_MEDIA))
    if page_count is not None:
        upper = min(upper, page_count)
    if upper <= 0:
        return PdfMedia((), 0, page_count)

    pages: list[MediaImage] = []
    total_b64 = 0
    with tempfile.TemporaryDirectory(prefix="galaxyhire-pdf-") as directory:
        prefix = str(Path(directory) / "page")
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    str(RENDER_DPI),
                    "-scale-to",
                    str(RENDER_SCALE),
                    "-f",
                    "1",
                    "-l",
                    str(upper),
                    "--",
                    str(source),
                    prefix,
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("could not render document pages") from exc

        for image_path in sorted(Path(directory).glob("page-*.png"))[:upper]:
            raw = image_path.read_bytes()
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                continue
            try:
                width, height = _png_size(raw)
            except ValueError:
                continue
            if width <= 0 or height <= 0 or width * height > MAX_RENDERED_PIXELS:
                continue
            encoded = base64.b64encode(raw).decode("ascii")
            if total_b64 + len(encoded) > MAX_TOTAL_BASE64_BYTES:
                break
            pages.append(MediaImage(encoded, "image/png", width, height))
            total_b64 += len(encoded)
    return PdfMedia(tuple(pages), len(pages), page_count)


def _image_data_url(image: MediaImage) -> str:
    return f"data:{image.media_type};base64,{image.data}"


def content_parts(protocol: str, text: str, media: PdfMedia | None) -> str | list[dict[str, Any]]:
    """Build the user content shape for Responses, Chat, or Anthropic APIs."""

    if not media or not media.images:
        return text
    key = (protocol or "").strip().lower()
    if key == "responses":
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        parts.extend({"type": "input_image", "image_url": _image_data_url(image), "detail": "auto"} for image in media.images)
        return parts
    if key in {"messages", "anthropic"}:
        parts = [{"type": "text", "text": text}]
        parts.extend(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image.media_type, "data": image.data},
            }
            for image in media.images
        )
        return parts
    # OpenAI Chat Completions (and compatible implementations) use this shape.
    parts = [{"type": "text", "text": text}]
    parts.extend({"type": "image_url", "image_url": {"url": _image_data_url(image), "detail": "auto"}} for image in media.images)
    return parts


def media_rejection(exc: Exception) -> bool:
    """Return true only for a likely client-side media rejection (not 429/auth)."""

    status = getattr(exc, "status_code", None)
    if status is None and getattr(exc, "response", None) is not None:
        status = getattr(exc.response, "status_code", None)
    if not isinstance(status, int) or not 400 <= status < 500 or status in {401, 403, 404, 429}:
        return False
    # Provider errors are safe to inspect only as a boolean; callers must not
    # log the text because it can echo prompt/document contents.
    message = str(exc).lower()
    return status in {400, 409, 415, 422} or any(
        token in message for token in ("image", "input_file", "input_image", "vision", "multimodal", "media", "content part")
    )
