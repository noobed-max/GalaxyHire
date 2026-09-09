"""Raw-snapshot persistence (docs/02 §3, docs/03 §6).

Every source response is persisted before normalize so we can re-derive when normalizer rules
change. v1 = filesystem object store; swap for S3 later behind the same call.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from galaxy.common.config import get_settings
from galaxy.models.enums import Site


def persist_raw(site: Site, slug_or_term: str, payload: object) -> str:
    """Write a gzipped JSON snapshot; return its object-store ref (relative path)."""
    settings = get_settings()
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (slug_or_term or "none"))
    rel = f"raw/{site.value}/{safe}/{ts}.json.gz"
    dest = Path(settings.object_store_dir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)
    return rel
