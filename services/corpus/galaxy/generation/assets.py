"""Generated-asset storage with signed, short-lived refs (docs/07 §3).

Resume/cover files are PII, so they are never a bare public stream. Each asset gets a random
128-bit ref and a TTL; `GET /assets/:ref` is API-key-guarded AND enforces the TTL via file mtime.
Stateless: the ref is the filename stem, content-type is inferred from the extension, so it
survives restarts without a registry table.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from galaxy.common.config import get_settings

TTL_SECONDS = 3600  # signed refs expire after an hour

_EXT_CONTENT_TYPE = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
}


def _assets_dir() -> Path:
    d = Path(get_settings().object_store_dir) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_asset(data: bytes, ext: str) -> str:
    """Persist bytes; return the signed ref (opaque token)."""
    ref = secrets.token_hex(16)  # 128-bit
    path = _assets_dir() / f"{ref}.{ext}"
    path.write_bytes(data)
    return ref


def load_asset(ref: str) -> tuple[Path, str] | None:
    """Return (path, content_type) if the ref exists and is within TTL; else None."""
    if not ref or not ref.isalnum():
        return None
    for path in _assets_dir().glob(f"{ref}.*"):
        if time.time() - path.stat().st_mtime > TTL_SECONDS:
            return None  # expired
        ext = path.suffix.lstrip(".")
        return path, _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    return None
