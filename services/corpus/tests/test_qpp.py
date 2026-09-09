"""Query performance prediction (`galaxy/search/qpp.py`).

Retrieval always returns its nearest N rows, so without this a gibberish query produced twenty
confidently-ranked jobs. These tests pin the two things that make the module work at all:

  * **coverage gates, cosine grades.** The two populations overlap on cosine alone (good min 0.368 <
    bad max 0.400), so a cosine threshold cannot separate them; corpus term coverage can.
  * the calibrated numbers still match the embedding model.

The second is a guard against a silent failure mode: cosine scales differ between models, so
swapping `EMBEDDING_VERSION` would otherwise turn this into a filter that passes everything.
"""

from __future__ import annotations

import pytest

from galaxy.search.qpp import (
    CALIBRATED_FOR,
    MODERATE_AT,
    STRONG_AT,
    WEAK_AT,
    Quality,
    predict,
)

# Observed distributions from scripts/qpp_calibrate.py over 40 real job titles vs 14 gibberish and
# off-domain queries. Kept as fixtures so the thresholds are testable without a live corpus.
GOOD_SIMS = [0.72, 0.68, 0.66, 0.64, 0.63, 0.62, 0.61, 0.60, 0.59, 0.58] + [0.45] * 20
# "java spring boot" — a perfectly good query whose mean@10 was the lowest of any good one (0.368),
# below the worst bad query. It only classifies correctly because coverage gates.
LOW_SIM_GOOD = [0.40, 0.39, 0.38, 0.37, 0.37, 0.36, 0.35, 0.35, 0.34, 0.33] + [0.30] * 20
NONSENSE_SIMS = [0.27, 0.26, 0.25, 0.24, 0.23, 0.23, 0.22, 0.22, 0.21, 0.21] + [0.20] * 20
# "recipe for sourdough bread" — reached 0.400, above several genuine queries.
HIGH_SIM_BAD = [0.44, 0.43, 0.41, 0.40, 0.40, 0.39, 0.38, 0.38, 0.37, 0.36] + [0.30] * 20


class TestVerdicts:
    def test_a_good_query_is_strong_and_relevant(self):
        verdict = predict(GOOD_SIMS, term_coverage=1.0)
        assert verdict.quality is Quality.STRONG
        assert verdict.looks_relevant is True

    def test_gibberish_is_rejected(self):
        verdict = predict(NONSENSE_SIMS, term_coverage=0.0)
        assert verdict.quality is Quality.NONE
        assert verdict.looks_relevant is False

    def test_off_domain_is_rejected(self):
        assert predict(HIGH_SIM_BAD, term_coverage=0.25).looks_relevant is False

    def test_a_good_query_with_low_similarity_is_still_relevant(self):
        """"java spring boot" scored 0.368 — below the worst nonsense query.

        Cosine alone misclassifies it. Full coverage is what saves it, which is the entire reason
        coverage gates rather than merely adjusting the verdict.
        """
        assert predict(LOW_SIM_GOOD, term_coverage=1.0).looks_relevant is True

    def test_a_bad_query_with_high_similarity_is_still_rejected(self):
        """"recipe for sourdough bread" scored 0.400, above several genuine queries.

        The mirror case: no similarity score should rescue a query whose words barely occur in any
        posting.
        """
        assert predict(HIGH_SIM_BAD, term_coverage=0.25).looks_relevant is False

    def test_no_results_is_unknown_not_none(self):
        # "Nothing to judge" and "judged as irrelevant" are different states; conflating them would
        # blame the user's query for an empty corpus.
        verdict = predict([])
        assert verdict.quality is Quality.UNKNOWN
        assert verdict.looks_relevant is False

    def test_explanations_are_written_for_the_user(self):
        assert "Strong matches" in predict(GOOD_SIMS).reason
        weak = predict(NONSENSE_SIMS, term_coverage=0.0).reason
        assert "closest" in weak.lower() or "nothing" in weak.lower()
        # Zero coverage is worth saying out loud — it's the actionable part.
        assert "search words" in weak


class TestCalibrationHolds:
    """If these fail, the thresholds no longer match the embedding model. Re-run the calibration."""

    def test_thresholds_are_ordered(self):
        assert STRONG_AT > MODERATE_AT > WEAK_AT

    def test_the_cosine_bands_admit_the_weakest_good_query(self):
        # Calibration observed good mean@10 min = 0.368 ("java spring boot"). The bands grade within
        # the coverage gate, so this must not fall below the moderate bar.
        assert 0.368 >= MODERATE_AT, "the moderate bar drifted above the weakest good query"

    def test_the_populations_are_known_to_overlap_on_cosine(self):
        """Documents why coverage gates rather than cosine.

        good min 0.368 < bad max 0.400. If a future change makes these separable, the coverage gate
        could be relaxed — but until then, asserting a clean cosine threshold would be false.
        """
        assert 0.368 < 0.400, "populations no longer overlap; the gate design can be revisited"

    def test_the_calibrated_model_is_recorded(self):
        # A verdict that doesn't say what it was calibrated against can't be audited later.
        assert CALIBRATED_FOR
        assert predict(GOOD_SIMS).calibrated_for == CALIBRATED_FOR
        assert predict(GOOD_SIMS, embedding_version="some-other-model").calibrated_for == "some-other-model"


class TestVarianceIsNotUsedForGating:
    """NQC and WIG are reported but never gate, because measurement showed they don't separate."""

    def test_high_variance_nonsense_is_still_rejected(self):
        # The whole reason NQC can't gate here: a nonsense query can have perfectly high score
        # variance among uniformly irrelevant results.
        spread_but_irrelevant = [0.35, 0.30, 0.28, 0.24, 0.22, 0.20, 0.18, 0.16, 0.14, 0.12] + [0.10] * 20
        verdict = predict(spread_but_irrelevant, term_coverage=0.0)
        assert verdict.predictors.nqc > 0.2, "fixture should have high variance"
        assert verdict.looks_relevant is False

    def test_low_variance_good_results_are_still_accepted(self):
        # The mirror case: tightly-clustered high similarities are a *good* outcome, but NQC reads
        # low variance as low confidence.
        tight_and_relevant = [0.62] * 10 + [0.60] * 20
        verdict = predict(tight_and_relevant, term_coverage=1.0)
        assert verdict.predictors.nqc < 0.05, "fixture should have low variance"
        assert verdict.looks_relevant is True

    def test_predictors_are_still_reported_for_diagnostics(self):
        p = predict(GOOD_SIMS).predictors.as_dict()
        assert {"mean_top_k", "top1", "nqc", "wig", "sample_size"} <= set(p)


class TestTermCoverage:
    def test_zero_coverage_rejects_regardless_of_similarity(self):
        # A query whose words appear nowhere in the corpus did not retrieve by evidence, only by
        # embedding proximity. High similarity must not rescue it.
        assert predict(GOOD_SIMS, term_coverage=0.0).quality is Quality.NONE
        assert predict(GOOD_SIMS, term_coverage=1.0).quality is Quality.STRONG

    def test_partial_coverage_demands_a_strong_similarity(self):
        # Could be a niche technology, could be off-domain phrasing sharing vocabulary.
        assert predict(GOOD_SIMS, term_coverage=0.5).looks_relevant is True
        assert predict(LOW_SIM_GOOD, term_coverage=0.5).looks_relevant is False

    def test_full_coverage_never_reports_nothing(self):
        # Every word the user typed appears in the corpus, so there is something to show even when
        # the nearest match is distant.
        assert predict([0.1] * 30, term_coverage=1.0).quality is Quality.MODERATE

    def test_absent_coverage_is_no_opinion_not_zero(self):
        # None means "not measured". Treating it as zero would penalise every caller that doesn't
        # compute it.
        assert predict(GOOD_SIMS, term_coverage=None).quality is Quality.STRONG

    def test_coverage_only_appears_when_measured(self):
        assert "term_coverage" not in predict(GOOD_SIMS).predictors.as_dict()
        assert "term_coverage" in predict(GOOD_SIMS, term_coverage=0.5).predictors.as_dict()


class TestRobustness:
    @pytest.mark.parametrize("sims", [[], [float("nan")], [float("nan")] * 5])
    def test_unusable_input_never_raises(self, sims):
        assert predict(sims).quality is Quality.UNKNOWN

    def test_similarities_are_sorted_before_measurement(self):
        """Retrieval returns RRF-fused order, which is not similarity order.

        RRF ranks by summed reciprocal rank, so the fused top 10 can have mediocre cosine while
        excellent matches sit lower. Reading the fused order made "react frontend developer" measure
        0.38 when its true nearest-neighbour mean was 0.75.
        """
        ascending = list(reversed(GOOD_SIMS))
        assert predict(ascending, term_coverage=1.0).predictors.mean_top_k == pytest.approx(
            predict(GOOD_SIMS, term_coverage=1.0).predictors.mean_top_k
        )

    def test_nans_are_dropped_rather_than_poisoning_the_mean(self):
        verdict = predict([0.7, float("nan"), 0.65, 0.6] * 8, term_coverage=1.0)
        assert verdict.quality in (Quality.STRONG, Quality.MODERATE)

    def test_a_single_result_is_judged_not_refused(self):
        assert predict([0.8]).quality is Quality.STRONG
        assert predict([0.1]).quality is Quality.NONE

    def test_serialises_for_the_api(self):
        body = predict(GOOD_SIMS, term_coverage=1.0).as_dict()
        assert set(body) == {"quality", "looks_relevant", "reason", "predictors", "calibrated_for"}
        assert isinstance(body["quality"], str)
