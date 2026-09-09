"""64-bit SimHash for description similarity (docs/03 §2).

Used to bucket same-company/title/location postings by description so genuinely distinct reqs
(e.g. Amazon's fifty simultaneous "SDE II — Seattle" openings) don't over-merge, while true
duplicates with near-identical descriptions still collapse.
"""

from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HASH_BITS = 64
_MASK = (1 << _HASH_BITS) - 1


def _token_hash(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    """Return a 64-bit SimHash of the text's token multiset."""
    if not text:
        return 0
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return 0
    vector = [0] * _HASH_BITS
    for tok in tokens:
        h = _token_hash(tok)
        for i in range(_HASH_BITS):
            vector[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(_HASH_BITS):
        if vector[i] > 0:
            out |= 1 << i
    return out & _MASK


def hamming(a: int, b: int) -> int:
    """Bit distance between two simhashes (0 = identical, 64 = opposite)."""
    return ((a ^ b) & _MASK).bit_count()


# Descriptions within this Hamming distance are treated as "the same posting".
# Above it, they are distinct reqs and get separate canonical ids.
# NOTE: SimHash similarity is total Hamming distance across all 64 bits, so we cluster by
# pairwise distance (see dedup.engine) rather than trying to derive a single stable bucket key —
# zeroing low bits is not a valid locality-sensitive hash.
SIMHASH_MERGE_THRESHOLD = 6
