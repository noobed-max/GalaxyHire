import asyncio
from unittest import mock

from ranking.service import RankingService


def test_ranking_service_builds_job_document():
    lead = {
        "title": "Backend Engineer",
        "company": "Acme",
        "url": "https://example.com/job",
        "description": "Build APIs.",
    }

    doc = RankingService.job_document(lead)

    assert "Job Title: Backend Engineer" in doc
    assert "Company: Acme" in doc
    assert "Description: Build APIs." in doc


def test_ranking_service_evaluates_lead():
    evaluator = mock.Mock()
    expected = {"score": 88, "reason": "Strong fit", "match_points": [], "gaps": []}
    evaluator.score.return_value = expected
    service = RankingService(evaluator=evaluator)

    result = asyncio.run(service.evaluate_lead({"title": "Role"}, {"skills": []}))

    assert result == expected
    assert "Job Title: Role" in evaluator.score.call_args.args[0]


def test_ranking_service_reevaluates_until_stopped():
    service = RankingService()
    stop = asyncio.Event()

    async def fake_evaluate(lead, profile):
        stop.set()
        return {"score": 70, "reason": "ok"}

    with mock.patch.object(service, "evaluate_lead", side_effect=fake_evaluate):
        result = asyncio.run(service.reevaluate_all([{"title": "A"}, {"title": "B"}], {}, stop_event=stop))

    assert result.total == 2
    assert result.scored == 1
    assert result.failed == 0


def test_ranking_service_uses_injected_scoring_engine():
    class FakeScoringEngine:
        def score(self, job, profile):
            return {"score": 77, "job": job, "profile": profile}

    service = RankingService(scoring_engine=FakeScoringEngine())

    result = asyncio.run(service.deterministic_score({"title": "Role"}, {"skills": []}))

    assert result["score"] == 77
    assert "Job Title: Role" in result["job"]


def test_ranking_service_uses_injected_semantic_matcher():
    semantic = mock.Mock()
    semantic.match.return_value = {"score": 64}
    service = RankingService(semantic=semantic)

    result = asyncio.run(service.semantic_match({"title": "Role"}, {"skills": []}))

    assert result == {"score": 64}
    assert "Job Title: Role" in semantic.match.call_args.args[0]
    assert semantic.match.call_args.kwargs["candidate_data"] == {"skills": []}


def test_ranking_service_uses_injected_feedback_ranker():
    feedback = mock.Mock()
    feedback.apply.return_value = {"signal_score": 70}
    service = RankingService(feedback=feedback)

    result = asyncio.run(service.apply_feedback({"signal_score": 60}, [{"feedback": "good"}]))

    assert result == {"signal_score": 70}
    feedback.apply.assert_called_once_with({"signal_score": 60}, [{"feedback": "good"}])
