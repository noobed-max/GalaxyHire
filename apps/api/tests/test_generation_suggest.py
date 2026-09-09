"""AI pick + ATS suggestions for the resume maker (Generate Resume flow).

The model chooses among exactly the tag-scoped evidence the pane shows and proposes
tightened rewordings. Unknown ids are dropped: a hallucinated pick must never enter the
selection state the renderer trusts.
"""

from __future__ import annotations


def _profile() -> dict:
    return {
        "skills": [
            {"id": "sk1", "n": "Python"},
            {"id": "sk2", "n": "Go"},
        ],
        "exp": [
            {
                "id": "e1", "role": "Backend Engineer", "co": "Acme",
                "d": "Built APIs.\nCut latency 40%.",
                "points": [
                    {"id": "p1", "text": "Built APIs."},
                    {"id": "p2", "text": "Cut latency 40%."},
                ],
            }
        ],
        "projects": [],
    }


def _lead() -> dict:
    return {"title": "Backend Engineer", "company": "Acme", "description": "Python APIs, low latency."}


class TestSuggestValidation:
    def test_unknown_ids_are_dropped(self, monkeypatch):
        import llm
        from generation.suggest import SelectionSuggestion, suggest_selection

        def fake_call_llm(s, u, m, step=None):
            return m(
                skills_on=["sk1", "ghost-skill"],
                experience_on=["e1"],
                projects_on=[],
                points_on=["p1", "ghost-point"],
                suggestions=[],
            )

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        result = suggest_selection(_profile(), _lead())
        assert isinstance(result, SelectionSuggestion)
        assert result.skills_on == ["sk1"]
        assert result.points_on == ["p1"]
        assert result.experience_on == ["e1"]

    def test_suggestions_keep_only_known_points_with_text(self, monkeypatch):
        import llm
        from generation.suggest import suggest_selection

        def fake_call_llm(s, u, m, step=None):
            return m(
                skills_on=[],
                experience_on=[],
                projects_on=[],
                points_on=["p1"],
                suggestions=[
                    {
                        "point_id": "p1", "parent_kind": "experience", "parent_id": "e1",
                        "suggested_text": "Built Python APIs serving 10k rps.",
                        "reason": "Mirrors the JD's Python/latency language.",
                    },
                    {"point_id": "ghost", "parent_kind": "experience", "parent_id": "e1",
                     "suggested_text": "Invented.", "reason": "Hallucinated."},
                    {"point_id": "p2", "parent_kind": "experience", "parent_id": "e1",
                     "suggested_text": "   ", "reason": "Empty."},
                ],
            )

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        result = suggest_selection(_profile(), _lead())
        assert len(result.suggestions) == 1
        assert result.suggestions[0].point_id == "p1"
        assert "Python" in result.suggestions[0].suggested_text

    def test_prompt_shows_only_the_evidence_catalog(self, monkeypatch):
        import llm
        from generation import suggest as suggest_module

        seen: dict = {}

        def fake_call_llm(s, u, m, step=None):
            seen["system"] = s
            seen["user"] = u
            seen["step"] = step
            return m()

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        suggest_module.suggest_selection(_profile(), _lead())
        assert "p1" in seen["user"] and "Built APIs." in seen["user"]
        assert seen["step"] == "generator"
        assert "VERBATIM" not in seen["system"]  # picking is not drafting; no verbatim claim here
