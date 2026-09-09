"""The apply flow (§6).

The load-bearing behaviour here is a distinction, not a feature: **preparing an application is not
applying to it.** Conflating them fills the pipeline with applications the user never submitted, and
it is the API-level counterpart of the extension's never-submit guard.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import apply as apply_router


class FakeLeads:
    def __init__(self, *, missing: bool = False, records: dict[str, dict] | None = None):
        self.events: list[tuple[str, str]] = []
        self.statuses: list[tuple[str, str]] = []
        self.missing = missing
        self.records = records or {}

    def record_event(self, job_id: str, action: str) -> None:
        self.events.append((job_id, action))

    def update_lead_status(self, job_id: str, status: str) -> None:
        if self.missing:
            raise LookupError(job_id)
        self.statuses.append((job_id, status))

    def get_lead_by_id(self, job_id: str) -> dict:
        return self.records.get(job_id, {})

    def get_all_leads(self) -> list[dict]:
        return list(self.records.values())

    def save_prepared_fill_context(self, job_id: str, context: dict) -> None:
        self.records[job_id].setdefault("source_meta", {})["prepared_fill_context"] = context


class FakeSettings:
    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get_settings(self) -> dict:
        return self._values


class FakeProfile:
    def get_profile(self) -> dict:
        return {
            "n": "Ada Lovelace",
            "s": "Builds reliable software.",
            "identity": {"email": "ada@example.com", "city": "Bengaluru"},
            "skills": [{"n": "Python"}, {"n": "React"}],
            "projects": [{"id": "p1", "title": "Compiler", "stack": ["Python"], "impact": "Built it."}],
            "exp": [{"role": "Engineer", "co": "Analytical Engines", "period": "2024", "d": "Built APIs"}],
        }


class FakeRepo:
    def __init__(self, leads: FakeLeads, settings: FakeSettings, profile: FakeProfile | None = None):
        self.leads = leads
        self.settings = settings
        self.profile = profile or FakeProfile()


class FakeCorpus:
    def __init__(self, context: dict | None = None, fill: dict | None = None):
        self.context = context if context is not None else {
            "job_id": "j1",
            "apply_url": "https://boards.example.com/apply/1",
            "resume_paths": {"pdf": "/home/u/resumes/acme-swe.pdf", "tex": "/home/u/resumes/acme-swe.tex"},
        }
        self.fill = fill
        self.prepare_calls: list[dict] = []

    async def prepare_application(self, job_id, *, check_live=True, output_dir=None):
        self.prepare_calls.append({"job_id": job_id, "check_live": check_live, "output_dir": output_dir})
        return self.context

    async def fill_context(self, url):
        return self.fill

    async def asset(self, ref):
        return b"%PDF-test", "application/pdf"


@pytest.fixture
def wired(monkeypatch):
    """A client plus the fakes it was wired with, so tests can assert on side effects."""

    def build(
        *,
        corpus: FakeCorpus | None = None,
        leads: FakeLeads | None = None,
        settings: FakeSettings | None = None,
        profile: FakeProfile | None = None,
    ):
        corpus = corpus or FakeCorpus()
        leads = leads or FakeLeads()
        settings = settings or FakeSettings()
        repo = FakeRepo(leads, settings, profile)
        monkeypatch.setattr(apply_router, "get_repository", lambda: repo)
        monkeypatch.setattr(apply_router, "create_corpus_client", lambda: corpus)
        app = FastAPI()
        app.include_router(apply_router.create_router())
        return TestClient(app), corpus, leads

    return build


class TestPreparingIsNotApplying:
    def test_apply_does_not_mark_the_lead_applied(self, wired):
        # The user has opened a form, not submitted one. Marking "applied" here would claim an
        # application that may never happen.
        client, _corpus, leads = wired()
        res = client.post("/api/v1/leads/j1/apply")
        assert res.status_code == 200
        assert leads.statuses == []

    def test_apply_records_that_the_portal_was_opened(self, wired):
        client, _corpus, leads = wired()
        client.post("/api/v1/leads/j1/apply")
        assert ("j1", "apply_opened") in leads.events

    def test_only_the_explicit_endpoint_sets_applied(self, wired):
        client, _corpus, leads = wired()
        res = client.post("/api/v1/leads/j1/applied")
        assert res.status_code == 200
        assert leads.statuses == [("j1", "applied")]

    def test_reporting_a_fill_does_not_mark_applied(self, wired):
        # The extension fills; it cannot submit, so it cannot advance the pipeline either.
        client, _corpus, leads = wired()
        client.post("/api/v1/leads/j1/filled", json={"filled": ["name", "email"], "skipped": ["cover"]})
        assert leads.statuses == []


class TestApply:
    def test_returns_the_apply_url_and_resume_paths(self, wired):
        client, _corpus, _leads = wired()
        body = client.post("/api/v1/leads/j1/apply").json()
        assert body["apply_url"] == "https://boards.example.com/apply/1"
        assert body["resume_paths"]["pdf"].endswith(".pdf")
        # §5: the editable source ships alongside the PDF.
        assert body["resume_paths"]["tex"].endswith(".tex")

    def test_passes_the_users_configured_folder_through(self, wired):
        client, corpus, _leads = wired(settings=FakeSettings({"resume_output_dir": "/home/u/Documents/resumes"}))
        client.post("/api/v1/leads/j1/apply")
        assert corpus.prepare_calls[0]["output_dir"] == "/home/u/Documents/resumes"

    def test_blank_folder_setting_falls_back_to_the_default(self, wired):
        # An empty string must not be forwarded as a path; the corpus should use its configured dir.
        client, corpus, _leads = wired(settings=FakeSettings({"resume_output_dir": "   "}))
        client.post("/api/v1/leads/j1/apply")
        assert corpus.prepare_calls[0]["output_dir"] is None

    def test_a_closed_posting_is_reported_not_silently_prepared(self, wired):
        # Applying to a dead posting wastes the user's time and pollutes the pipeline.
        client, _corpus, leads = wired(corpus=FakeCorpus(context={"expired": True}))
        res = client.post("/api/v1/leads/j1/apply")
        assert res.status_code == 409
        assert leads.events == []

    def test_a_failed_audit_write_does_not_fail_the_apply(self, wired, monkeypatch):
        # Losing an audit line must not break the thing the user is doing.
        class Exploding(FakeLeads):
            def record_event(self, job_id, action):
                raise RuntimeError("disk full")

        client, _corpus, _leads = wired(leads=Exploding())
        assert client.post("/api/v1/leads/j1/apply").status_code == 200

    def test_manual_job_uses_gateway_resume_and_persists_extension_context(self, wired, tmp_path):
        resume = tmp_path / "manual-resume.pdf"
        resume.write_bytes(b"%PDF")
        record = {
            "job_id": "manual1",
            "platform": "manual",
            "title": "Software Engineer",
            "company": "Acme",
            "url": "https://jobs.acme.test/apply/1",
            "resume_asset": str(resume),
            "selected_projects": ["p1"],
            "source_meta": {},
        }
        leads = FakeLeads(records={"manual1": record})
        client, corpus, _leads = wired(leads=leads)

        prepared = client.post("/api/v1/leads/manual1/apply")
        assert prepared.status_code == 200
        assert corpus.prepare_calls == []
        assert prepared.json()["fill_context"]["identity"]["email"] == "ada@example.com"
        assert prepared.json()["fill_context"]["resume_url"] == "/api/v1/leads/manual1/pdf"

        context = client.get(
            "/api/v1/fill-context", params={"url": "https://jobs.acme.test/application/questions"}
        )
        assert context.status_code == 200
        assert context.json()["job_id"] == "manual1"

    def test_scraped_job_with_tailored_resume_uses_gateway_resume(self, wired, tmp_path):
        resume = tmp_path / "tailored-resume.pdf"
        resume.write_bytes(b"%PDF-scraped")
        record = {
            "job_id": "scraped1",
            "platform": "greenhouse:anthropic",
            "title": "Backend Engineer",
            "company": "Anthropic",
            "url": "https://boards.greenhouse.io/anthropic/jobs/1",
            "resume_asset": str(resume),
            "selected_projects": ["p1"],
            "source_meta": {},
        }
        leads = FakeLeads(records={"scraped1": record})
        client, corpus, _leads = wired(leads=leads)

        prepared = client.post("/api/v1/leads/scraped1/apply")
        assert prepared.status_code == 200
        assert corpus.prepare_calls == []
        assert prepared.json()["fill_context"]["resume_url"] == "/api/v1/leads/scraped1/pdf"
        assert prepared.json()["fill_context"]["company"] == "Anthropic"


class TestFillContext:
    def test_returns_the_prepared_context(self, wired):
        client, _corpus, _leads = wired(corpus=FakeCorpus(fill={"job_id": "j1", "company": "Acme"}))
        res = client.get("/api/v1/fill-context", params={"url": "https://boards.example.com/apply/1"})
        assert res.status_code == 200
        assert res.json()["company"] == "Acme"

    def test_an_unmatched_url_is_a_404_not_a_wrong_context(self, wired):
        # Returning *some* context for an unrecognised page would fill a form with another job's
        # answers, which is worse than filling nothing.
        client, _corpus, _leads = wired(corpus=FakeCorpus(fill=None))
        assert client.get("/api/v1/fill-context", params={"url": "https://elsewhere.test/x"}).status_code == 404

    def test_corpus_context_gets_a_gateway_resume_download_url(self, wired):
        fill = {"job_id": "j1", "company": "Acme", "resume_ref": "abc123"}
        client, _corpus, _leads = wired(corpus=FakeCorpus(fill=fill))
        body = client.get("/api/v1/fill-context", params={"url": "https://boards.example/x"}).json()
        assert body["resume_url"] == "/api/v1/corpus-assets/abc123"

    def test_gateway_proxies_the_authenticated_corpus_asset(self, wired):
        client, _corpus, _leads = wired()
        res = client.get("/api/v1/corpus-assets/abc123")
        assert res.status_code == 200
        assert res.content == b"%PDF-test"
        assert res.headers["content-type"] == "application/pdf"


class TestFilledReporting:
    def test_counts_are_recorded_for_the_activity_log(self, wired):
        client, _corpus, leads = wired()
        res = client.post(
            "/api/v1/leads/j1/filled", json={"filled": ["name", "email", "phone"], "skipped": ["cover"]}
        )
        assert res.json() == {"ok": True, "filled": 3, "skipped": 1}
        # "filled 3/4" is how the user learns which portals the extension handles badly.
        assert any("3/4" in action for _job, action in leads.events)

    def test_an_empty_report_is_accepted(self, wired):
        client, _corpus, _leads = wired()
        assert client.post("/api/v1/leads/j1/filled", json={}).status_code == 200


class TestAppliedErrors:
    def test_unknown_lead_is_a_404(self, wired):
        client, _corpus, _leads = wired(leads=FakeLeads(missing=True))
        assert client.post("/api/v1/leads/nope/applied").status_code == 404


class TestManualHandoffHelpers:
    """Pure coverage for the local-job path that does not need TestClient's worker thread."""

    def test_builds_a_resume_download_and_profile_payload(self, tmp_path):
        resume = tmp_path / "manual-resume.pdf"
        resume.write_bytes(b"%PDF")
        lead = {
            "job_id": "manual1",
            "title": "Software Engineer",
            "company": "Acme",
            "url": "https://jobs.acme.test/apply/1",
            "resume_asset": str(resume),
            "selected_projects": ["p1"],
        }
        repo = FakeRepo(FakeLeads(), FakeSettings())

        context = apply_router._manual_fill_context(repo, lead)

        assert context["identity"]["email"] == "ada@example.com"
        assert context["resume_url"] == "/api/v1/leads/manual1/pdf"
        assert context["projects"][0]["title"] == "Compiler"

    def test_unmatched_host_never_reuses_another_jobs_answers(self):
        context = {
            "job_id": "manual1",
            "apply_url": "https://jobs.acme.test/apply/1",
            "prepared_at": "2026-07-29T00:00:00+00:00",
        }
        leads = FakeLeads(
            records={"manual1": {"source_meta": {"prepared_fill_context": context}}}
        )
        repo = FakeRepo(leads, FakeSettings())

        assert apply_router._local_fill_context(repo, "https://other.test/apply") is None
        assert apply_router._local_fill_context(repo, "https://jobs.acme.test/questions") == context


class TestFillDecide:
    """Value decisions for the extension run here (backend + LLM), never in the page."""

    def _decide(self, wired, monkeypatch, decision, capture):
        import llm

        def fake_call_llm(s, u, m, step=None):
            capture["system"] = s
            capture["user"] = u
            return decision

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        client, *_ = wired()
        return client

    def test_decides_values_from_the_stored_profile(self, wired, monkeypatch):
        decision = apply_router.FillDecision(
            fills=[
                apply_router.FillDecisionLine(id=1, value="Ada Lovelace"),
                apply_router.FillDecisionLine(id=2, value="ignored-but-unknown-field"),
            ]
        )
        capture: dict = {}
        client = self._decide(wired, monkeypatch, decision, capture)
        res = client.post(
            "/api/v1/fill/decide",
            json={
                "job_id": "j1",
                "fields": [
                    {"id": 1, "label": "Full name", "kind": "text", "type": "text", "required": True},
                    {"id": 5, "label": "City", "kind": "text", "type": "text"},
                ],
            },
        )
        assert res.status_code == 200
        # ids the page never showed are dropped — the model must not invent fields
        assert res.json()["fills"] == [{"id": 1, "value": "Ada Lovelace"}]
        assert "Ada Lovelace" in capture["user"]
        assert "[1] text \"Full name\" (required)" in capture["user"]

    def test_llm_failure_degrades_to_empty_fills(self, wired, monkeypatch):
        import llm

        def boom(s, u, m, step=None):
            raise RuntimeError("provider down")

        monkeypatch.setattr(llm, "call_llm", boom)
        client, *_ = wired()
        res = client.post(
            "/api/v1/fill/decide",
            json={"fields": [{"id": 1, "label": "Name", "kind": "text", "type": "text"}]},
        )
        assert res.status_code == 200
        assert res.json() == {"fills": []}

    def test_empty_field_list_short_circuits_without_an_llm_call(self, wired, monkeypatch):
        import llm

        def never(*a, **k):
            raise AssertionError("must not be called")

        monkeypatch.setattr(llm, "call_llm", never)
        client, *_ = wired()
        res = client.post("/api/v1/fill/decide", json={"fields": []})
        assert res.json() == {"fills": []}
