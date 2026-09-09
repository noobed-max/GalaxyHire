"""Learned search preference (`galaxy/search/preference.py`).

`search_feedback` was written to from the first migration and read by nothing — `record_feedback`
claimed the effect was "applied elsewhere" and there was no elsewhere. These tests cover the read
side, and most of them exist to pin what it deliberately *refuses* to infer.

The failure mode for this kind of feature is not a crash. It is a filter bubble the user cannot
see: results quietly disappear, the corpus looks thin, and nothing indicates that their own past
clicks caused it. So the restraint is the feature.
"""

from __future__ import annotations

import pytest

from galaxy.models.enums import Seniority
from galaxy.search.planner import compile_query
from galaxy.search.preference import (
    GOOD,
    INFER_AT,
    NOT_RELEVANT,
    TOO_JUNIOR,
    TOO_SENIOR,
    Preference,
    infer_bounds,
)


class TestSuppression:
    def test_one_rejection_is_enough_to_hide_that_job(self):
        # Unambiguous: the user saw this posting and said no. Nothing to generalise from, and
        # nothing to be cautious about — it applies to exactly the job it was given on.
        pref = Preference(suppressed_job_ids=frozenset({"abc"}))
        assert pref.is_active
        assert "abc" in pref.suppressed_job_ids

    def test_suppression_reaches_the_sql_layer_as_an_exclusion(self):
        # Must be an exclusion in the query, not a post-filter: dropping rows after retrieval would
        # silently shrink every page by however many the user had rejected.
        compiled = compile_query(
            search_term="engineer", preference=Preference(suppressed_job_ids=frozenset({"a", "b"}))
        )
        assert sorted(compiled.negative.job_ids) == ["a", "b"]


class TestBoundsAreInferredCautiously:
    def test_a_single_too_senior_infers_nothing(self):
        """One click is as likely to be a mis-titled posting as a statement about the user.

        The corpus demonstrably mislabels seniority — that is why levels.py exists — so acting on a
        single signal would let one bad title silently cap everything the user sees.
        """
        assert infer_bounds({TOO_SENIOR: 1}) == (None, None)

    def test_repeated_too_senior_caps_the_band(self):
        assert infer_bounds({TOO_SENIOR: INFER_AT}) == (Seniority.MID, None)

    def test_repeated_too_junior_raises_the_floor(self):
        assert infer_bounds({TOO_JUNIOR: INFER_AT}) == (None, Seniority.MID)

    def test_contradictory_signals_infer_nothing_rather_than_a_majority(self):
        """A user who has said both is describing a band this scheme cannot represent.

        Resolving by majority would filter out the middle they are actually asking for, and would
        do it more confidently the more feedback they gave.
        """
        assert infer_bounds({TOO_SENIOR: INFER_AT + 5, TOO_JUNIOR: INFER_AT}) == (None, None)

    def test_good_and_not_relevant_never_move_the_band(self):
        # "Not relevant" says nothing about level — it is most often about the role or the stack.
        assert infer_bounds({NOT_RELEVANT: 50, GOOD: 50}) == (None, None)


class TestExplicitRequestsWin:
    """An inferred bound may fill a gap; it may never overrule what the user just typed."""

    def test_an_explicit_max_seniority_is_not_replaced_by_inference(self):
        compiled = compile_query(
            search_term="engineer",
            max_seniority="lead",
            preference=Preference(max_seniority=Seniority.MID),
        )
        assert compiled.negative.seniority_above is Seniority.LEAD

    def test_inference_applies_when_the_request_is_silent(self):
        compiled = compile_query(
            search_term="engineer", preference=Preference(max_seniority=Seniority.MID)
        )
        assert compiled.negative.seniority_above is Seniority.MID

    def test_suppression_is_not_overridable_by_the_query(self):
        # Unlike bounds: naming exact rejected jobs cannot be contradicted by a search term.
        compiled = compile_query(
            search_term="senior staff principal engineer",
            max_seniority="exec",
            preference=Preference(suppressed_job_ids=frozenset({"x"})),
        )
        assert compiled.negative.job_ids == ["x"]


class TestItSaysWhatItDid:
    def test_an_inert_preference_claims_nothing(self):
        assert Preference().describe() == []
        assert Preference().is_active is False

    def test_every_effect_is_reported(self):
        pref = Preference(
            suppressed_job_ids=frozenset({"a", "b"}),
            max_seniority=Seniority.MID,
            min_seniority=Seniority.JUNIOR,
        )
        reasons = " ".join(pref.describe())
        assert "2 jobs" in reasons
        assert "above mid" in reasons
        assert "below junior" in reasons

    def test_the_singular_reads_correctly(self):
        # Small thing, but this string is shown to the user on every search.
        assert "1 job you marked" in Preference(suppressed_job_ids=frozenset({"a"})).describe()[0]

    def test_serialises_for_the_api(self):
        body = Preference(suppressed_job_ids=frozenset({"a"}), max_seniority=Seniority.MID).as_dict()
        assert body["active"] is True
        assert body["suppressed"] == 1
        assert body["max_seniority"] == "mid"
        assert body["reasons"]


class TestNoUserMeansNoEffect:
    @pytest.mark.asyncio
    async def test_an_anonymous_search_is_unchanged(self):
        # Guards against the preference layer requiring a user: an anonymous search must behave
        # exactly as it did before this module existed, without touching the database.
        from galaxy.search.preference import load_preference

        pref = await load_preference(None)
        assert pref.is_active is False
        assert pref.as_dict()["reasons"] == []
