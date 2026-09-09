"""Job level extraction (`galaxy/ingestion/levels.py`).

Seniority, not years, is the primary signal — most postings never state years, but the level is
almost always in the title. These tests cover the four failure classes the previous extractor had,
each measured on a real 4,225-job corpus, plus the ladder codes it never recognised.
"""

from __future__ import annotations

import pytest

from galaxy.ingestion.levels import (
    Confidence,
    extract_level,
    matches_max_seniority,
    matches_years,
    seniority_to_years,
    years_to_seniority,
)
from galaxy.models.enums import Seniority


class TestTheRegressionsThatMotivatedThis:
    """Four classes of wrongness, all quantified against the real corpus."""

    @pytest.mark.parametrize(
        "title",
        [
            "Relationship Manager, Scale",
            "Solutions Engineer - LATAM (Portuguese Speaking)",
            "Technology Enablement Program Manager",
            "Sales Operations Manager, Sales Development",
        ],
    )
    def test_a_description_saying_lead_does_not_make_a_lead_role(self, title: str):
        """741 of 1,343 `lead` labels came from description text like this.

        The description says "you will lead the team"; the role is not a lead position. Bare word
        presence in free prose is inadmissible.
        """
        description = (
            "You will lead cross-functional initiatives and architect solutions with staff engineers."
        )
        assert extract_level(title, description).seniority is None

    def test_a_description_mentioning_a_director_does_not_make_an_exec_role(self):
        # 211 of 399 `exec` labels came from this.
        description = "Reporting to the Director of Engineering, working with the Head of Product."
        assert extract_level("Backend Engineer", description).seniority is None

    def test_engineer_two_is_mid_not_senior(self):
        # `II`/`III` were mapped to senior. "Engineer II" is mid on every ladder that uses it.
        assert extract_level("Software Engineer II").seniority is Seniority.MID
        assert extract_level("Software Engineer III").seniority is Seniority.SENIOR

    def test_mid_is_reachable_at_all(self):
        # No pattern could previously produce `mid`, so the band existed but was never assigned.
        for title in ["Mid-Level Backend Developer", "Intermediate Software Engineer", "Mid Data Engineer"]:
            assert extract_level(title).seniority is Seniority.MID, title


class TestLadderCodes:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Software Engineer, L3", Seniority.JUNIOR),
            ("Software Engineer, L4", Seniority.MID),
            ("Software Engineer, L5", Seniority.SENIOR),
            ("Software Engineer, L6", Seniority.LEAD),
            ("Engineer E5", Seniority.SENIOR),
            ("Engineer IC3", Seniority.MID),
            ("Engineer IC4", Seniority.SENIOR),
            ("SDE I", Seniority.JUNIOR),
            ("SDE II", Seniority.MID),
            ("SDE III", Seniority.SENIOR),
            ("SWE II", Seniority.MID),
        ],
    )
    def test_reads_industry_ladders(self, title: str, expected: Seniority):
        assert extract_level(title).seniority is expected

    def test_a_ladder_code_is_strong_evidence(self):
        assert extract_level("Engineer L5").confidence is Confidence.STRONG

    def test_a_bare_numeral_is_weak_evidence(self):
        # A trailing numeral can be a team name or product version, so it must not outrank a word.
        assert extract_level("Software Engineer II").confidence is Confidence.WEAK

    @pytest.mark.parametrize("title", ["HTML5 Developer", "Postgres 15 DBA", "Vue 3 Engineer"])
    def test_version_numbers_are_not_ladder_codes(self, title: str):
        # A naive \\d pattern reads "HTML5" as L5. The word boundary is what prevents it.
        assert extract_level(title).seniority is not Seniority.SENIOR


class TestSpecificityBeatsOrder:
    def test_senior_staff_engineer_is_lead(self):
        # First-match-wins ordering returned senior here. Staff is the more senior claim.
        assert extract_level("Senior Staff Software Engineer").seniority is Seniority.LEAD

    def test_associate_director_is_exec_not_junior(self):
        # "Associate" reads junior alone, but not when a director title contains it.
        assert extract_level("Associate Director, Data Platform").seniority is Seniority.EXEC

    def test_associate_engineer_is_still_junior(self):
        assert extract_level("Associate Software Engineer").seniority is Seniority.JUNIOR

    def test_a_title_word_beats_an_anchored_description_phrase(self):
        verdict = extract_level("Principal Engineer", "This is a junior-level position.")
        assert verdict.seniority is Seniority.LEAD
        assert verdict.source == "title"


class TestRegionalAndNegated:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Werkstudent Software Development", Seniority.INTERN),
            # The English rendering of the same thing; found unlabelled in the real corpus.
            ("Working Student Language & Localization", Seniority.INTERN),
            ("Praktikum Data Science", Seniority.INTERN),
            ("Stagiaire Développeur", Seniority.INTERN),
            ("Software Engineer Fresher", Seniority.JUNIOR),
            ("Graduate Software Engineer", Seniority.JUNIOR),
            ("Apprentice Developer", Seniority.JUNIOR),
        ],
    )
    def test_non_english_and_regional_wording(self, title: str, expected: Seniority):
        assert extract_level(title).seniority is expected

    def test_no_experience_required_reads_as_junior(self):
        # The user's case: a junior role that never states years.
        verdict = extract_level("Software Engineer", "No prior experience is required for this role.")
        assert verdict.seniority is Seniority.JUNIOR


class TestAnchoredDescriptions:
    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("This is a senior-level role on the platform team.", Seniority.SENIOR),
            ("We are hiring at the L5 level.", Seniority.SENIOR),
            ("Level: staff", Seniority.LEAD),
            ("This is a mid-level position.", Seniority.MID),
        ],
    )
    def test_explicit_assertions_are_admitted(self, description: str, expected: Seniority):
        assert extract_level("Software Engineer", description).seniority is expected

    def test_an_anchored_phrase_is_medium_confidence(self):
        verdict = extract_level("Software Engineer", "This is a senior-level role.")
        assert verdict.confidence is Confidence.MEDIUM
        assert verdict.source == "description"


class TestYearsBridge:
    """Years remain useful as corroboration — just not as the primary signal."""

    @pytest.mark.parametrize(
        ("years", "expected"),
        [(0, Seniority.JUNIOR), (1, Seniority.JUNIOR), (3, Seniority.MID),
         (6, Seniority.SENIOR), (10, Seniority.LEAD)],
    )
    def test_years_imply_a_level(self, years: int, expected: Seniority):
        assert years_to_seniority(years) is expected

    def test_a_level_implies_a_years_band_not_a_point(self):
        # Published ladder guidance disagrees at the edges, so a band is the honest representation.
        low, high = seniority_to_years(Seniority.MID)
        assert low == 2 and high == 5
        assert seniority_to_years(Seniority.LEAD)[1] is None, "senior bands are open-ended"

    def test_stated_years_are_a_last_resort_when_no_level_word_exists(self):
        verdict = extract_level("Backend Engineer", None, stated_years=7)
        assert verdict.seniority is Seniority.SENIOR
        assert verdict.source == "years"

    def test_a_level_word_outranks_stated_years(self):
        # A posting titled "Junior" asking for 8 years is contradictory; trust the title.
        verdict = extract_level("Junior Backend Engineer", None, stated_years=8)
        assert verdict.seniority is Seniority.JUNIOR

    def test_years_in_the_title_are_read(self):
        assert extract_level("Backend Engineer (5+ years)").seniority is Seniority.SENIOR

    def test_a_stated_zero_years_is_treated_as_no_signal(self):
        """extract_min_years floors months and takes the minimum match, so 0 is an artefact.

        A real corpus row titled "Senior Forward Deployed Engineer" carried min_years_experience = 0
        because its description mentioned a sub-year duration. Reading that as entry-level would
        invert the seniority of the very roles it appears on.
        """
        assert extract_level("Sales Development Representative", None, stated_years=0).seniority is None
        # And it must not drag a titled role downwards either.
        assert extract_level("Senior Engineer", None, stated_years=0).seniority is Seniority.SENIOR


class TestFiltering:
    def test_max_seniority_excludes_more_senior_roles(self):
        assert matches_max_seniority(Seniority.MID, Seniority.MID) is True
        assert matches_max_seniority(Seniority.JUNIOR, Seniority.MID) is True
        assert matches_max_seniority(Seniority.LEAD, Seniority.MID) is False

    def test_an_unknown_level_passes(self):
        # Unknown is not the same as excluded; dropping every unlabelled job would hide most of the
        # corpus. The fix for unknowns is better extraction, not a stricter filter.
        assert matches_max_seniority(None, Seniority.JUNIOR) is True

    def test_years_filtering_works_from_the_level_alone(self):
        # The case the old code could not handle: no stated years, so years filtering was skipped
        # entirely and a senior role passed a "2 years" search.
        assert matches_years(Seniority.SENIOR, None, wanted_years=2) is False
        assert matches_years(Seniority.SENIOR, None, wanted_years=6) is True

    def test_years_filtering_works_from_stated_years_alone(self):
        assert matches_years(None, 5, wanted_years=3) is False
        assert matches_years(None, 5, wanted_years=7) is True

    def test_the_stricter_of_the_two_signals_wins(self):
        """Reversed after measuring the corpus.

        This originally asserted that stated years win, on the reasoning that a posting should be
        judged on what it asked for. The data disagreed: postings titled "Senior Software Engineer"
        asking for 5+ and 7+ years had years extracted as 2 and 0, and trusting those let them pass a
        "max 2 years" search. The two signals fail in opposite directions and the title is the more
        reliable one, so the floor is the max of both.
        """
        assert matches_years(Seniority.SENIOR, 2, wanted_years=3) is False
        assert matches_years(Seniority.SENIOR, 2, wanted_years=6) is True
        # A stated requirement above the band's floor still tightens the filter.
        assert matches_years(Seniority.MID, 8, wanted_years=5) is False

    def test_experience_above_a_band_is_not_disqualifying(self):
        # Someone with 12 years is over-qualified for a mid role, not ineligible.
        assert matches_years(Seniority.MID, None, wanted_years=12) is True


class TestAuditability:
    def test_the_verdict_records_its_evidence(self):
        verdict = extract_level("Staff Engineer, L6")
        assert verdict.evidence
        assert verdict.source == "title"
        # Every signal is kept so a surprising call can be traced.
        assert len(verdict.signals) >= 2

    def test_serialises_for_storage_and_api(self):
        body = extract_level("Senior Engineer").as_dict()
        assert body["seniority"] == "senior"
        assert body["years_band"] == [5, 9]

    def test_an_unmatched_title_is_honest_about_it(self):
        verdict = extract_level("Data Engineer")
        assert verdict.seniority is None
        assert verdict.years_band is None


class TestIngestWiring:
    """apply_derived_fields must feed years into the level extractor, not run them independently."""

    def test_a_years_only_posting_gets_a_level_at_ingest(self):
        """Level coverage came out ~13 points low because nothing fed the years bridge.

        `extract_min_years` and `extract_seniority` ran side by side, so a posting stating "6+ years
        of experience" with no level word in its title kept a NULL seniority even though levels.py
        could have inferred one.
        """
        from galaxy.ingestion.normalize import apply_derived_fields
        from galaxy.models.job import Location, RawJobFields

        fields = RawJobFields(
            title="Backend Engineer",
            company="Acme",
            location=Location(),
            description_md="We are looking for someone with 6+ years of experience building services.",
        )
        apply_derived_fields(fields)
        assert fields.min_years_experience == 6
        assert fields.seniority is Seniority.SENIOR

    def test_a_source_supplied_level_is_not_overwritten(self):
        # Boards like solid.jobs publish experienceLevel directly; that outranks inference.
        from galaxy.ingestion.normalize import apply_derived_fields
        from galaxy.models.job import Location, RawJobFields

        fields = RawJobFields(
            title="Senior Backend Engineer",
            company="Acme",
            location=Location(),
            seniority=Seniority.MID,
            description_md="10+ years required.",
        )
        apply_derived_fields(fields)
        assert fields.seniority is Seniority.MID
