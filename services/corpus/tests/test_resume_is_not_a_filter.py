"""The résumé must not decide which jobs a user can see.

The user's instruction was blunt — "do not ever use the users resume as a filter kind of thing on
jobs" — then refined: rank on résumé skills, but at *lower priority* than an explicit filter. "let's
say the level is set to beginner or entry level, then that's a higher priority."

The failure this prevents is subtle and bad: a search that quietly returns "jobs like my CV" instead
of "jobs matching what I typed". Someone trying to move into a new stack cannot find those jobs at
all, and nothing on screen explains why.
"""

from __future__ import annotations

from uuid import uuid4

from galaxy.models.enums import Seniority
from galaxy.search.planner import compile_query
from galaxy.search.ranker import RESUME_WEIGHT_KEYS, WEIGHTS


class TestTheQueryOutranksTheCV:
    def test_query_relevance_dominates_resume_signal(self):
        """It used to be the other way round.

        skill_overlap 0.30 + graph_proof 0.15 = 0.45 of résumé against 0.30 for the query, which is
        why searching "python backend" surfaced Go roles for a profile that happened to list Go.
        """
        resume = sum(WEIGHTS[k] for k in RESUME_WEIGHT_KEYS)
        assert WEIGHTS["semantic"] > resume * 2, (
            f"résumé signal ({resume}) must stay well below query relevance "
            f"({WEIGHTS['semantic']}) — it is a tiebreaker, not a selector"
        )

    def test_resume_still_counts_for_something(self):
        # The user asked for résumé-based ranking, just weak. Zeroing it would ignore that half of
        # the instruction as surely as leaving it dominant ignored the other half.
        assert sum(WEIGHTS[k] for k in RESUME_WEIGHT_KEYS) > 0

    def test_weights_still_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


class TestEligibilityIgnoresTheProfile:
    def test_an_explicit_filter_is_not_softened_by_the_profile(self):
        # A hard filter decides eligibility in SQL. No amount of skill overlap may reinstate a job
        # the user excluded — "level is set to beginner" outranks everything.
        compiled = compile_query(search_term="engineer", max_seniority="junior")
        assert compiled.negative.seniority_above is Seniority.JUNIOR

    def test_the_profile_slice_is_not_folded_into_query_skills(self):
        """Retrieval must build its skill leg from the query alone.

        `compiled.slice` still exists — the ranker uses it to explain matched/missing skills — but
        unioning it into the retrieval skill set is what made the CV decide eligibility, and with an
        empty query its skills became the embedding source outright.
        """
        from galaxy.models.profile import Profile, Skill

        profile = Profile(user_id=uuid4(), skills=[Skill(name="Rust"), Skill(name="Kubernetes")])
        compiled = compile_query(search_term="frontend react developer", profile=profile)

        # The slice is still populated for the explanation...
        assert compiled.slice.skill_names
        # ...but the query's own skills are what retrieval is given, and they do not include the CV.
        assert "rust" not in {s.lower() for s in compiled.positive.skills}
