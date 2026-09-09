"""Collision-proof canonical id (docs/03 §2).

canonicalJobId = sha256( normCompany | US | normTitle | US | normLocation | US | descBucket )

- Content-derived, so the same role from LinkedIn and from the company's ATS produces the SAME
  id and merges into one record (the core collision requirement).
- The description-simhash bucket keeps genuinely distinct same-title reqs separate (the inverse,
  over-merge, failure — docs/03 §2 Flaw 1).
"""

from __future__ import annotations

import hashlib

from galaxy.ingestion.normalize import norm_company, norm_location, norm_title
from galaxy.models.job import RawJobFields

_US = "\x1f"  # unit separator — a delimiter that can't appear in content


def base_group_key(fields: RawJobFields) -> str:
    """The company|title|location key — the coarse grouping before description clustering.

    Two observations with the same base key but different canonical ids are the over-merge guard
    firing — we count these to measure the split rate (docs/03 §2, docs/10 Phase 1 exit).
    """
    nc = norm_company(fields.company)
    nt = norm_title(fields.title)
    nl = norm_location(fields.location.city, fields.location.country, fields.location.remote)
    return _US.join([nc, nt, nl])


def canonical_job_id(fields: RawJobFields, disambiguator: str = "") -> str:
    """Derive the stable canonical id from the base key + a description-cluster disambiguator.

    The `disambiguator` (empty for a lone posting) separates genuinely distinct reqs that share
    company|title|location but have dissimilar descriptions (docs/03 §2). The dedup engine assigns
    it by Hamming-distance clustering; a single posting or a clean duplicate pair uses "".
    """
    digest = hashlib.sha256(f"{base_group_key(fields)}{_US}{disambiguator}".encode()).hexdigest()
    return digest
