"""Query performance prediction — "did this query actually retrieve anything good?"

Retrieval always returns its nearest N rows. Nothing in the pipeline previously said whether those
rows were *relevant*, so a gibberish query produced twenty confidently-ranked jobs. This module
answers that question without any labelled relevance data, which is the problem
[query performance prediction](https://dl.acm.org/doi/10.1145/1390334.1390376) exists to solve.

## What we measured

Calibrated against three populations on a real 4,225-job corpus: 30 job titles sampled from the
corpus, 15 keyword-style queries a user would actually type ("devops terraform aws"), and 10
gibberish/off-domain queries.

**Cosine similarity alone does not separate them.**

| population | mean@10 min | median | max |
|---|---|---|---|
| good (titles + keyword queries) | **0.368** | 0.60 | 0.75 |
| gibberish + off-domain | 0.083 | 0.233 | **0.400** |

The good minimum (0.368, from "java spring boot") sits *below* the bad maximum (0.400, from "recipe
for sourdough bread"), so no single cosine threshold classifies both correctly. An earlier version of
this module claimed a clean gap; that was an artefact of calibrating only on job titles, which are
fluent noun phrases that embed close to job documents. Real keyword queries embed as a blend that
sits further from any single posting, and adding them collapsed the gap.

**Corpus term coverage does separate them**, cleanly:

| population | coverage min | max |
|---|---|---|
| good | **1.00** | 1.00 |
| gibberish + off-domain | 0.00 | **0.67** |

Which makes sense: a real job-search query is built from words that occur in job postings. So
coverage is the primary gate and cosine refines within it — the opposite of the original design.

**The textbook predictors don't work here either.** NQC (Shtok et al., normalized standard deviation
of top-k scores) and WIG (Zhou & Croft, top-k mean minus a corpus baseline) both overlap completely
between populations. They were built for lexical retrieval where scores are unbounded and
incomparable between queries, so they normalize by a corpus score and read the distribution's
*shape*. Cosine in a shared embedding space is already bounded and comparable, so that normalization
is pointless and the variance intuition misleads: a nonsense query can have high score variance among
uniformly irrelevant results. Both are still computed and reported as diagnostics; nothing gates on
them. This matches the published caution about
[QPP for neural IR](https://ceur-ws.org/Vol-3478/paper04.pdf).

## Why not the fused score

The fused RRF score cannot be used for this at all. RRF sums `1/(k + rank)`, so it is derived from
*ranks* rather than similarities and its distribution is near-identical whatever you search for.
Measured: the nonsense query "underwater basket weaving zookeeper" scored **0.500** while "kubernetes
platform engineer" scored **0.456**. Any threshold on that number rejects good queries and admits
garbage. The signal has to come from underneath the fusion.

## The decision rule

Coverage gates, cosine grades:

    coverage >= 0.75    in-domain      -> cosine bands decide strong / moderate / weak
    coverage 0.34-0.74   uncertain     -> needs a higher cosine to be called relevant
    coverage <= 0.33     out of domain -> never relevant, whatever the cosine

Coverage is not required to be exactly 1.0 even though every good query scored that here: a
legitimate query can name a technology this corpus happens not to contain, and that should reduce
confidence rather than disqualify the search.

**These numbers are specific to `all-MiniLM-L6-v2` and to a corpus of this size.** Cosine scales
differ between embedding models and coverage rises with corpus size, so changing either invalidates
them; `scripts/qpp_calibrate.py` re-measures and `tests/test_qpp.py` asserts the separation still
holds, so a model swap fails loudly rather than silently degrading into a filter that passes
everything.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

# Cosine bands, calibrated for all-MiniLM-L6-v2. Read the module docstring before changing these:
# they grade *within* the coverage gate and cannot separate the populations on their own.
STRONG_AT = 0.55
MODERATE_AT = 0.36
WEAK_AT = 0.25

# Coverage gate — the primary discriminator. Good queries all scored 1.00, bad ones topped out at
# 0.67, so the boundary sits between with room for a legitimate query naming something this corpus
# happens not to contain.
COVERAGE_IN_DOMAIN = 0.75
COVERAGE_OUT_OF_DOMAIN = 0.33
# What an uncertain-coverage query must reach on cosine to still count as relevant.
UNCERTAIN_COVERAGE_NEEDS = 0.55

# Averaged over the top 10 rather than the top 1: a single outlier row shouldn't decide the verdict.
PRIMARY_K = 10
# NQC/WIG need a baseline drawn from further down the list.
TAIL_K = 20

# The embedding model these thresholds were calibrated against. Recorded in the verdict so a
# mismatch is visible in logs and API responses rather than silently wrong.
CALIBRATED_FOR = "minilm-l6-v2"


class Quality(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass
class Predictors:
    """Raw predictor values, kept for diagnostics and for re-calibration."""

    mean_top_k: float = 0.0
    top1: float = 0.0
    # Reported, not acted on — see the module docstring.
    nqc: float = 0.0
    wig: float = 0.0
    # Fraction of query terms that occur anywhere in the corpus. Model-independent, so it catches
    # invented words that the embedder will still happily place somewhere in vector space.
    term_coverage: float | None = None
    sample_size: int = 0

    def as_dict(self) -> dict:
        out = {
            "mean_top_k": round(self.mean_top_k, 4),
            "top1": round(self.top1, 4),
            "nqc": round(self.nqc, 4),
            "wig": round(self.wig, 4),
            "sample_size": self.sample_size,
        }
        if self.term_coverage is not None:
            out["term_coverage"] = round(self.term_coverage, 3)
        return out


@dataclass
class QualityVerdict:
    quality: Quality
    predictors: Predictors = field(default_factory=Predictors)
    reason: str = ""
    calibrated_for: str = CALIBRATED_FOR

    @property
    def looks_relevant(self) -> bool:
        """Whether results are worth presenting as answers rather than as nearest neighbours."""
        return self.quality in (Quality.STRONG, Quality.MODERATE)

    def as_dict(self) -> dict:
        return {
            "quality": str(self.quality),
            "looks_relevant": self.looks_relevant,
            "reason": self.reason,
            "predictors": self.predictors.as_dict(),
            "calibrated_for": self.calibrated_for,
        }


def _band(mean_top_k: float) -> Quality:
    """Cosine grading, used only after the coverage gate has been applied."""
    if mean_top_k >= STRONG_AT:
        return Quality.STRONG
    if mean_top_k >= MODERATE_AT:
        return Quality.MODERATE
    if mean_top_k >= WEAK_AT:
        return Quality.WEAK
    return Quality.NONE


def _decide(mean_top_k: float, term_coverage: float | None) -> Quality:
    """Coverage gates, cosine grades.

    Coverage leads because it is what actually separates the populations: every good query in
    calibration had coverage 1.00 while the worst bad one reached 0.67, whereas the cosine
    distributions overlap. Cosine then distinguishes a close match from a distant one *within* the
    in-domain set, which coverage cannot do — "java spring boot" and "kubernetes platform engineer"
    both have full coverage but 0.37 and 0.67 similarity.
    """
    graded = _band(mean_top_k)

    if term_coverage is None:
        # Nothing measured, so cosine is all there is. Kept deliberately permissive rather than
        # penalising callers that don't compute coverage.
        return graded

    if term_coverage <= COVERAGE_OUT_OF_DOMAIN:
        # Out of domain. This is the case cosine alone got wrong: "recipe for sourdough bread"
        # reached 0.40, above several genuine queries, but only a quarter of its words appear in any
        # posting. No similarity score should rescue it.
        return Quality.NONE

    if term_coverage < COVERAGE_IN_DOMAIN:
        # Partially recognised — could be a niche technology, could be off-domain phrasing that
        # happens to share vocabulary. Demand a strong similarity before calling it relevant.
        return graded if mean_top_k >= UNCERTAIN_COVERAGE_NEEDS else Quality.WEAK

    # In domain. Never report worse than moderate: every word the user typed appears in the corpus,
    # so there is something to show even when the nearest match is not close.
    return graded if graded is not Quality.NONE else Quality.MODERATE


def predict(
    similarities: list[float],
    *,
    term_coverage: float | None = None,
    embedding_version: str | None = None,
) -> QualityVerdict:
    """Judge retrieval quality from the cosine similarities of the ranked results.

    `similarities` must be in rank order (best first) and must be **cosine similarities**, not fused
    RRF scores — passing the latter produces a meaningless verdict, for the reason in the module
    docstring.

    `term_coverage` is optional but valuable: it is the only model-independent signal here, and it is
    what separates "you searched for something this corpus doesn't contain" from "your wording is
    unusual but the meaning landed".
    """
    # Sorted descending, not taken in rank order. Retrieval hands back RRF-fused order, and RRF
    # ranks by summed reciprocal rank rather than by similarity — so the fused top 10 can have
    # mediocre cosine while excellent matches sit further down. Reading the fused order made
    # "react frontend developer" score 0.38 when its true nearest-neighbour mean was 0.75.
    usable = sorted(
        (s for s in similarities if isinstance(s, (int, float)) and not math.isnan(s)), reverse=True
    )
    if not usable:
        return QualityVerdict(Quality.UNKNOWN, reason="No scored results to judge.")

    top_k = usable[:PRIMARY_K]
    mean_top_k = statistics.mean(top_k)
    tail = usable[-TAIL_K:] if len(usable) > TAIL_K else usable
    overall_mean = statistics.mean(usable)

    predictors = Predictors(
        mean_top_k=mean_top_k,
        top1=usable[0],
        # NQC: standard deviation of the top band, normalized by the overall mean.
        nqc=(statistics.pstdev(usable[:TAIL_K]) / overall_mean) if overall_mean > 1e-9 else 0.0,
        # WIG: how far the top band sits above the tail baseline.
        wig=mean_top_k - statistics.mean(tail),
        term_coverage=term_coverage,
        sample_size=len(usable),
    )

    quality = _decide(mean_top_k, term_coverage)

    return QualityVerdict(
        quality=quality,
        predictors=predictors,
        reason=_explain(quality, predictors),
        calibrated_for=embedding_version or CALIBRATED_FOR,
    )


def _explain(quality: Quality, p: Predictors) -> str:
    """A sentence the UI can show. Written for the user, not for a developer."""
    coverage_note = ""
    if p.term_coverage is not None and p.term_coverage <= 0.0:
        coverage_note = " None of your search words appear anywhere in the collected jobs."

    if quality is Quality.STRONG:
        return f"Strong matches (similarity {p.mean_top_k:.2f})."
    if quality is Quality.MODERATE:
        return f"Reasonable matches (similarity {p.mean_top_k:.2f}), but not close ones.{coverage_note}"
    if quality is Quality.WEAK:
        return (
            f"Probably nothing relevant here (similarity {p.mean_top_k:.2f}). "
            f"These are the closest jobs in the collection, not real matches.{coverage_note}"
        )
    return (
        f"Nothing in the collection matches this (similarity {p.mean_top_k:.2f}). "
        f"Check the spelling, or scrape for this role first.{coverage_note}"
    )
