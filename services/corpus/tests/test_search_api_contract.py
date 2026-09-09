"""The corpus search endpoint's request contract.

These are deliberately validation-only, so they need no database: a rejected body never reaches
the handler. They exist because of a failure mode that is invisible in the response.

Posting `{"query": "..."}` instead of `{"search_term": "..."}` used to be accepted. Pydantic
dropped the unknown field, leaving an *empty* search — and an empty search is not an error, it
returns a full page of profile-ranked jobs with confident fit scores. Three different queries came
back with byte-identical results, which is the only reason it was noticed at all. Nothing in the
payload said "your query was ignored".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from galaxy.api.app import app
from galaxy.common.config import get_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def key() -> str:
    return next(iter(get_settings().api_key_set))


class TestUnknownFieldsAreRejected:
    def test_the_exact_typo_that_caused_this(self, client: TestClient, key: str):
        res = client.post("/search", headers={"x-api-key": key}, json={"query": "python", "limit": 5})
        assert res.status_code == 422
        # The response must name the offending field, or it isn't actionable.
        assert "query" in res.text

    @pytest.mark.parametrize(
        "body",
        [
            {"searchTerm": "python"},          # camelCase from a JS client
            {"search_terms": "python"},        # plural
            {"q": "python"},                   # the other common spelling
            {"max_experience": 3},             # plausible but wrong name for max_years
        ],
    )
    def test_near_miss_field_names_fail_loudly(self, client: TestClient, key: str, body: dict):
        assert client.post("/search", headers={"x-api-key": key}, json=body).status_code == 422

    def test_feedback_rejects_unknown_fields_too(self, client: TestClient, key: str):
        # Same class of bug: a mistyped signal field would silently record nothing useful.
        res = client.post(
            "/feedback",
            headers={"x-api-key": key},
            json={"canonical_job_id": "abc", "signal": "good", "reason": "nice"},
        )
        assert res.status_code == 422

    def test_parse_rejects_unknown_fields_too(self, client: TestClient, key: str):
        res = client.post("/search/parse", headers={"x-api-key": key}, json={"txt": "python jobs"})
        assert res.status_code == 422


class TestValidRequestsStillPass:
    """Guard against over-tightening: every field the real client sends must remain accepted."""

    def test_the_payload_apps_api_actually_sends_is_accepted(self, client: TestClient, key: str):
        # Mirrors apps/api/corpus/client.py::search exactly. If this 422s, that client is broken.
        body = {
            "search_term": "python",
            "positive_skills": [],
            "negative_titles": [],
            "max_years": None,
            "max_seniority": None,
            "remote": None,
            "location": None,
            "limit": 5,
            "sort": "relevance",
        }
        res = client.post("/search", headers={"x-api-key": key}, json=body)
        assert res.status_code != 422, res.text

    def test_an_empty_body_is_still_valid(self, client: TestClient, key: str):
        # "Show me everything" is a legitimate request — the browse case with no query typed.
        # Strictness must reject unknown *names*, not require any particular field to be present.
        assert client.post("/search", headers={"x-api-key": key}, json={}).status_code != 422


class TestAuthStillGuardsTheEndpoint:
    def test_a_missing_key_is_401_not_422(self, client: TestClient):
        # Validation must not shadow auth: an unauthenticated caller should learn it needs a key,
        # not receive a critique of its request body.
        assert client.post("/search", json={"query": "nope"}).status_code == 401
