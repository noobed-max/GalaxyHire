"""Corpus-backed discovery — the two-engine pipeline from ARCHITECTURE.md D1.

Retrieval (the corpus) selects and primarily ranks candidates; this app's scoring engine explains
the best slice and contributes a bounded tie-break. These tests cover that seam, profile-shape
coercion, and what happens when the corpus is down or empty. The corpus's own retrieval quality is
tested in `services/corpus`.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from corpus.client import CorpusUnavailable
from corpus.service import (
    CorpusDiscoveryService,
    coerce_profile_for_scoring,
    corpus_job_to_lead,
)


class FakeClient:
    """Stands in for the corpus HTTP service."""

    def __init__(self, jobs=None, stats=None, fail=False):
        self._jobs = jobs if jobs is not None else []
        self._stats = stats or {"canonical_jobs": 100, "searchable": 100, "pending_embedding": 0}
        self._fail = fail
        self.search_calls: list[dict] = []

    async def search(self, **kwargs):
        if self._fail:
            raise CorpusUnavailable("corpus unreachable at http://127.0.0.1:8100")
        self.search_calls.append(kwargs)
        return self._jobs

    async def scrape_start(self, phrase, hours=24, location="", portals=None):
        self.scrape_start_calls = getattr(self, "scrape_start_calls", [])
        self.scrape_start_calls.append(
            {"phrase": phrase, "hours": hours, "location": location, "portals": portals}
        )
        return {"running": True, "phrase": phrase}

    async def scrape_fresh(self, phrase, location="", portals=None):
        self.scrape_fresh_calls = getattr(self, "scrape_fresh_calls", [])
        self.scrape_fresh_calls.append({"phrase": phrase, "location": location, "portals": portals})
        return False

    async def scrape_sources(self):
        return {
            "available": True,
            "sources": [
                {"id": "arbeitnow", "label": "Arbeitnow", "kind": "board",
                 "recency_policy": "strict", "default_enabled": True},
                {"id": "jobicy", "label": "Jobicy", "kind": "board",
                 "recency_policy": "strict", "default_enabled": True},
                {"id": "echojobs", "label": "EchoJobs", "kind": "board",
                 "recency_policy": "off", "default_enabled": False},
            ],
        }

    async def stats(self):
        if self._fail:
            raise CorpusUnavailable("down")
        return self._stats

    async def parse_query(self, text):
        if self._fail:
            raise CorpusUnavailable("down")
        return {"search_term": text, "max_seniority": "mid"}


class FakeRanking:
    """Scores by how many profile skills appear in the job document."""

    def __init__(self, explode_on: set[str] | None = None):
        self.explode_on = explode_on or set()
        self.seen: list[dict] = []

    async def evaluate_lead(self, lead, profile, settings=None, use_llm=False):
        if lead["job_id"] in self.explode_on:
            raise RuntimeError("scoring blew up")
        self.seen.append(lead)
        text = f"{lead['title']} {lead['description']}".lower()
        skills = [str(s.get("n", "")).lower() for s in profile.get("skills", [])]
        hits = [s for s in skills if s and s in text]
        return {"score": 10 * len(hits), "reason": f"matched {len(hits)} skills", "criteria": {"stack": len(hits)}}


GRAPH_PROFILE = {"skills": [{"n": "Kubernetes", "cat": "tool"}, {"n": "Go", "cat": "language"}]}


def job(jid: str, title: str, *, description: str = "", site: str = "greenhouse", **extra):
    return {
        "canonical_job_id": jid,
        "title": title,
        "company": "Acme",
        "url": f"https://example.com/{jid}",
        "description_md": description,
        "site": site,
        "location": {"city": "Berlin", "country": "Germany", "remote": False},
        **extra,
    }


class TestLeadProjection:
    def test_maps_corpus_fields_onto_the_lead_shape(self):
        lead = corpus_job_to_lead(job("j1", "Platform Engineer", description="Go and Kubernetes"))
        assert lead["job_id"] == "j1"
        assert lead["title"] == "Platform Engineer"
        assert lead["description"] == "Go and Kubernetes"
        assert lead["kind"] == "job"

    def test_flattens_structured_location(self):
        assert corpus_job_to_lead(job("j1", "X"))["location"] == "Berlin, Germany"

    def test_marks_remote_in_the_location_text(self):
        lead = corpus_job_to_lead(job("j1", "X", location={"city": "Berlin", "remote": True}))
        assert lead["location"].startswith("Remote")

    def test_survives_a_missing_location(self):
        assert corpus_job_to_lead({"canonical_job_id": "j1", "title": "X"})["location"] == ""

    def test_carries_provenance_for_the_ui_and_apply_flow(self):
        lead = corpus_job_to_lead(
            job("j1", "X", seniority="senior", min_years_experience=5, fit_score=0.83)
        )
        assert lead["source_meta"]["site"] == "greenhouse"
        assert lead["source_meta"]["seniority"] == "senior"
        assert lead["source_meta"]["min_years_experience"] == 5
        assert lead["source_meta"]["retrieval_score"] == 0.83
        assert lead["score"] == 83

    def test_does_not_invent_a_profile_score(self):
        assert "signal_score" not in corpus_job_to_lead(job("j1", "X"))

    def test_carries_the_24h_claim_for_the_ui_badge(self):
        # MAJOR-CHANGE/05 §3: 'posted' vs 'first_seen' must survive to the lead so the card can
        # badge them differently; absent stays absent (no invented claim).
        assert corpus_job_to_lead(job("j1", "X", freshness="posted"))["source_meta"]["freshness"] == "posted"
        assert corpus_job_to_lead(job("j1", "X", freshness="first_seen"))["source_meta"]["freshness"] == "first_seen"
        assert corpus_job_to_lead(job("j1", "X"))["source_meta"]["freshness"] is None


class TestProfileCoercion:
    def test_leaves_a_graph_shaped_profile_untouched(self):
        assert coerce_profile_for_scoring(GRAPH_PROFILE) is GRAPH_PROFILE

    def test_converts_plain_string_skills(self):
        # scoring_engine calls skill.get("n") with no fallback, so strings raise AttributeError.
        out = coerce_profile_for_scoring({"skills": ["Python", "Go"]})
        assert out["skills"] == [{"n": "Python", "cat": "general"}, {"n": "Go", "cat": "general"}]

    def test_converts_the_api_storage_shape(self):
        # normalize_profile_payload emits {"name", "category"}; the engine wants {"n", "cat"}.
        out = coerce_profile_for_scoring({"skills": [{"name": "Rust", "category": "language"}]})
        assert out["skills"] == [{"n": "Rust", "cat": "language"}]

    def test_preserves_other_profile_keys(self):
        out = coerce_profile_for_scoring({"skills": ["Go"], "desired_position": "SRE"})
        assert out["desired_position"] == "SRE"

    @pytest.mark.parametrize("profile", [{}, {"skills": []}, {"skills": "nonsense"}])
    def test_passes_through_anything_it_cannot_interpret(self, profile):
        assert coerce_profile_for_scoring(profile) == profile


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_retrieval_order_when_reranking_is_off(self):
        client = FakeClient([job("j1", "B role"), job("j2", "A role")])
        svc = CorpusDiscoveryService(client, FakeRanking())
        res = await svc.search(query="role", rerank=False)
        assert [x["job_id"] for x in res.leads] == ["j1", "j2"]
        assert res.retrieved == 2 and res.reranked == 0

    @pytest.mark.asyncio
    async def test_query_relevance_stays_primary_over_profile_fit(self):
        # A strong résumé match must not displace a substantially better query result.
        client = FakeClient([
            job("query-best", "Engineer", description="mostly java", fit_score=0.90),
            job("profile-best", "Engineer", description="Kubernetes and Go all day", fit_score=0.40),
        ])
        svc = CorpusDiscoveryService(client, FakeRanking())
        res = await svc.search(query="engineer", profile=GRAPH_PROFILE)
        assert [x["job_id"] for x in res.leads] == ["query-best", "profile-best"]
        assert res.leads[1]["signal_score"] > res.leads[0]["signal_score"]
        assert res.leads[0]["signal_reason"]
        assert res.reranked == 2

    @pytest.mark.asyncio
    async def test_profile_fit_can_break_a_close_query_relevance_tie(self):
        client = FakeClient([
            job("weak", "Engineer", description="mostly java", fit_score=0.61),
            job("strong", "Engineer", description="Kubernetes and Go all day", fit_score=0.60),
        ])
        svc = CorpusDiscoveryService(client, FakeRanking())
        res = await svc.search(query="engineer", profile=GRAPH_PROFILE)
        assert [x["job_id"] for x in res.leads] == ["strong", "weak"]

    @pytest.mark.asyncio
    async def test_deep_evaluation_is_bounded_without_hiding_the_tail(self):
        jobs = [
            job(f"j{i}", "Engineer", description="Go", fit_score=(100 - i) / 100)
            for i in range(5)
        ]
        ranking = FakeRanking()
        svc = CorpusDiscoveryService(FakeClient(jobs), ranking, profile_eval_limit=2)
        res = await svc.search(query="engineer", profile=GRAPH_PROFILE)
        assert [lead["job_id"] for lead in ranking.seen] == ["j0", "j1"]
        assert [lead["job_id"] for lead in res.leads[2:]] == ["j2", "j3", "j4"]
        assert all(not lead["source_meta"]["profile_evaluated"] for lead in res.leads[2:])
        assert res.retrieved == 5
        assert res.reranked == 2

    @pytest.mark.asyncio
    async def test_a_lead_whose_scoring_fails_is_kept_not_dropped(self):
        # Losing a real job because one criterion threw is worse than showing it unscored.
        client = FakeClient([job("ok", "Engineer", description="Go"), job("boom", "Engineer")])
        svc = CorpusDiscoveryService(client, FakeRanking(explode_on={"boom"}))
        res = await svc.search(query="engineer", profile=GRAPH_PROFILE)
        assert {x["job_id"] for x in res.leads} == {"ok", "boom"}

    @pytest.mark.asyncio
    async def test_skips_reranking_without_a_profile(self):
        ranking = FakeRanking()
        svc = CorpusDiscoveryService(FakeClient([job("j1", "X")]), ranking)
        res = await svc.search(query="x")
        assert res.reranked == 0
        assert ranking.seen == []

    @pytest.mark.asyncio
    async def test_forwards_filters_as_corpus_filters(self):
        # These narrow already-collected jobs; they are never scrape parameters (D7).
        client = FakeClient([])
        svc = CorpusDiscoveryService(client, FakeRanking())
        await svc.search(query="swe", max_seniority="mid", max_years=3, remote=True, location="Berlin")
        call = client.search_calls[0]
        assert call["max_seniority"] == "mid"
        assert call["max_years"] == 3
        assert call["remote"] is True
        assert call["location"] == "Berlin"

    @pytest.mark.asyncio
    async def test_forwards_current_collection_boundary_to_corpus(self):
        client = FakeClient([])
        svc = CorpusDiscoveryService(client, FakeRanking())
        await svc.search(query="swe", rerank=False, observed_after="2026-09-09T10:00:00+00:00")
        assert client.search_calls[0]["observed_after"] == "2026-09-09T10:00:00+00:00"


class TestDegradation:
    @pytest.mark.asyncio
    async def test_a_down_corpus_degrades_instead_of_raising(self):
        # Profile, pipeline, and graph all work without the corpus, so search must not 500.
        svc = CorpusDiscoveryService(FakeClient(fail=True), FakeRanking())
        res = await svc.search(query="anything", profile=GRAPH_PROFILE)
        assert res.corpus_available is False
        assert res.leads == []
        assert "unreachable" in (res.note or "")

    @pytest.mark.asyncio
    async def test_distinguishes_an_empty_corpus_from_a_bad_query(self):
        svc = CorpusDiscoveryService(FakeClient([], stats={"canonical_jobs": 0, "searchable": 0, "pending_embedding": 0}))
        res = await svc.search(query="swe")
        assert "empty" in res.note.lower()

    @pytest.mark.asyncio
    async def test_calls_out_an_unembedded_corpus(self):
        # Full corpus, nothing searchable: the user should wait for embedding, not scrape again.
        svc = CorpusDiscoveryService(
            FakeClient([], stats={"canonical_jobs": 500, "searchable": 0, "pending_embedding": 500})
        )
        res = await svc.search(query="swe")
        assert "embedded" in res.note.lower()
        assert "500" in res.note

    @pytest.mark.asyncio
    async def test_reports_a_genuine_no_match_against_a_healthy_corpus(self):
        svc = CorpusDiscoveryService(FakeClient([]))
        res = await svc.search(query="zookeeper")
        assert "no matches" in res.note.lower()

    @pytest.mark.asyncio
    async def test_query_parse_falls_back_when_the_corpus_is_down(self):
        svc = CorpusDiscoveryService(FakeClient(fail=True))
        assert await svc.parse_query("senior go role") == {"search_term": "senior go role"}

    @pytest.mark.asyncio
    async def test_stats_report_availability(self):
        assert (await CorpusDiscoveryService(FakeClient()).stats())["available"] is True
        assert (await CorpusDiscoveryService(FakeClient(fail=True)).stats())["available"] is False

    @pytest.mark.asyncio
    async def test_scrape_history_clear_is_exposed_for_data_reset(self):
        class ClearableClient(FakeClient):
            async def scrape_clear_history(self):
                return {"cleared": 3, "stopped": False}

        result = await CorpusDiscoveryService(ClearableClient()).scrape_clear_history()
        assert result == {"available": True, "cleared": 3, "stopped": False}


class TestQueryRelevance:
    """Neither score in the pipeline measures query relevance — this is the guard for that.

    Established by measurement against a real corpus: the corpus's RRF `fit_score` gave the
    nonsense query "underwater basket weaving zookeeper" 0.500 while "kubernetes platform
    engineer" got 0.456, because RRF fuses ranks rather than similarities. The re-rank
    `signal_score` measures profile fit, so a Backend Developer posting scores well for a platform
    profile no matter what was searched. Result: 50 confidently-scored irrelevant jobs.
    """

    def test_drops_stopwords_but_keeps_meaningful_short_terms(self):
        from corpus.service import query_terms

        assert query_terms("a role in the go team") == ["team"]
        # "go" is 2 chars so it falls to the length floor; that's a known, accepted limitation.
        assert "kubernetes" in query_terms("senior kubernetes engineer")

    def test_counts_overlap_across_title_description_and_stack(self):
        from corpus.service import count_query_overlap

        lead = {"title": "Platform Engineer", "description": "We run Kubernetes", "tech_stack": ["Terraform"]}
        assert count_query_overlap(lead, ["platform"]) == 1
        assert count_query_overlap(lead, ["kubernetes"]) == 1
        assert count_query_overlap(lead, ["terraform"]) == 1
        assert count_query_overlap(lead, ["cobol"]) == 0

    @pytest.mark.asyncio
    async def test_flags_a_query_nothing_matches(self):
        client = FakeClient([job("j1", "Sanitation Associate", description="Cleaning duties")])
        res = await CorpusDiscoveryService(client).search(query="kubernetes terraform")
        assert res.query_matches == 0
        assert "closest semantic matches" in res.note

    @pytest.mark.asyncio
    async def test_stays_quiet_when_results_do_match(self):
        client = FakeClient([job("j1", "Platform Engineer", description="Kubernetes at scale")])
        res = await CorpusDiscoveryService(client).search(query="kubernetes")
        assert res.query_matches == 1
        assert res.note is None

    @pytest.mark.asyncio
    async def test_unknown_role_still_annotates_rather_than_filtering(self):
        # Semantic retrieval earns its keep for unknown/niche roles. Strict title eligibility is
        # applied only to a role family whose aliases are explicitly defined.
        client = FakeClient([job("j1", "Sanitation Associate"), job("j2", "Warehouse Sorter")])
        res = await CorpusDiscoveryService(client).search(query="kubernetes")
        assert len(res.leads) == 2
        assert all(x["query_overlap"] == 0 for x in res.leads)

    @pytest.mark.asyncio
    async def test_software_role_drops_semantic_neighbours_outside_the_title_family(self):
        client = FakeClient([
            job("bad", "Production Associate", description="factory operations"),
            job("good", "Backend Developer", description="services and APIs"),
        ])
        res = await CorpusDiscoveryService(client).search(query="software engineer")
        assert [lead["job_id"] for lead in res.leads] == ["good"]
        assert res.retrieved == 1

    @pytest.mark.asyncio
    async def test_no_query_means_no_relevance_claim(self):
        client = FakeClient([job("j1", "Anything")])
        res = await CorpusDiscoveryService(client).search(query=None)
        assert res.note is None


class TestEmptyBodyResponses:
    """A 204 is a success, and `.json()` on an empty body is not.

    The corpus answers POST /feedback with 204 No Content. The client parsed every response as
    JSON, so recording feedback raised JSONDecodeError *after* the row had already been inserted —
    the caller saw a 500 and the write had happened. That is the worst shape this bug can take: a
    user who retries duplicates their own signal, and a client that treats 500 as "try again"
    does so automatically.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status, body", [(204, b""), (200, b"")])
    async def test_a_bodyless_success_is_an_empty_dict_not_a_crash(self, status: int, body: bytes):
        import httpx

        from corpus.client import CorpusClient

        transport = httpx.MockTransport(lambda req: httpx.Response(status, content=body))
        client = CorpusClient()

        real = httpx.AsyncClient

        def _patched(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        httpx.AsyncClient = _patched
        try:
            assert await client._post("/feedback", {"a": 1}) == {}
            assert await client._get("/feedback") == {}
        finally:
            httpx.AsyncClient = real

    @pytest.mark.asyncio
    async def test_an_error_status_still_raises_rather_than_returning_empty(self):
        # The empty-body shortcut must not swallow failures: a 500 with no body is still a failure.
        import httpx

        from corpus.client import CorpusClient

        transport = httpx.MockTransport(lambda req: httpx.Response(500, content=b""))
        client = CorpusClient()
        real = httpx.AsyncClient

        def _patched(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        httpx.AsyncClient = _patched
        try:
            with pytest.raises(CorpusUnavailable):
                await client._post("/feedback", {"a": 1})
        finally:
            httpx.AsyncClient = real


class TestPlainEnglishConstraintsReachTheCorpus:
    """"software engineer, no 3+ years, no SDE 2, no SDE 3" must exclude, not attract.

    The whole sentence used to be sent as the *search term*. Embeddings have no notion of "no", so
    every excluded thing became something to match toward — asking for no SDE 2 pulled SDE 2 roles
    up the page. The corpus already had a parser for this; nothing called it.
    """

    @pytest.mark.asyncio
    async def test_negative_phrases_are_sent_and_not_dropped(self):
        # They were parsed and then discarded: neither the client payload nor the service signature
        # carried them, so the negatives were computed and silently thrown away.
        sent: dict = {}

        class _Client:
            async def search(self, **kwargs):
                sent.update(kwargs)
                return []

            async def stats(self):
                # An empty result makes the service ask why, to distinguish "no jobs" from
                # "not embedded yet". Without this the fake raises and hides the real assertion.
                return {"canonical_jobs": 100, "searchable": 100, "pending_embedding": 0}

        from corpus.service import CorpusDiscoveryService

        svc = CorpusDiscoveryService(_Client())
        await svc.search(query="software engineer", negative_phrases=["SDE 2", "SDE 3"])
        assert sent["negative_phrases"] == ["SDE 2", "SDE 3"]

    @pytest.mark.asyncio
    async def test_the_client_puts_them_in_the_request_body(self):
        import httpx

        from corpus.client import CorpusClient

        captured: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"results": []})

        transport = httpx.MockTransport(_handler)
        real = httpx.AsyncClient

        def _patched(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        httpx.AsyncClient = _patched
        try:
            await CorpusClient().search(search_term="engineer", negative_phrases=["SDE 3"])
        finally:
            httpx.AsyncClient = real
        assert captured["negative_phrases"] == ["SDE 3"]


class TestStopScanActuallyStops:
    """The Stop button had never worked, for anyone.

    `/scan` awaited `_run_search` directly instead of going through the TaskRegistry, so no task was
    ever registered under the search name. `/scan/stop` then looked for a running task, found none,
    and reported "No search running" while a search was visibly in progress.
    """

    @pytest.mark.asyncio
    async def test_join_waits_for_completion(self):
        from api.task_registry import TaskRegistry

        done = []

        async def _work(stop):
            await asyncio.sleep(0.01)
            done.append(True)

        reg = TaskRegistry()
        assert await reg.start("s", _work)
        await reg.join("s")
        assert done == [True], "join returned before the task finished"

    @pytest.mark.asyncio
    async def test_join_does_not_deadlock_on_the_registry_lock(self):
        # The task's own wrapper takes the same lock in its `finally`, so awaiting while holding it
        # would hang forever. This is the test that catches that.
        from api.task_registry import TaskRegistry

        async def _work(stop):
            await asyncio.sleep(0)

        reg = TaskRegistry()
        await reg.start("s", _work)
        await asyncio.wait_for(reg.join("s"), timeout=2.0)

    @pytest.mark.asyncio
    async def test_a_registered_search_can_be_stopped(self):
        from api.task_registry import TaskRegistry

        observed = {}

        async def _work(stop):
            for _ in range(200):
                if stop.is_set():
                    observed["stopped"] = True
                    return
                await asyncio.sleep(0.01)

        reg = TaskRegistry()
        await reg.start("corpus_search", _work)
        await asyncio.sleep(0.02)
        assert await reg.stop("corpus_search") is True, "stop found no running task"
        await reg.join("corpus_search")
        assert observed.get("stopped") is True

    @pytest.mark.asyncio
    async def test_stopping_nothing_reports_false(self):
        # The honest negative: with no search running, stop must say so rather than claim success.
        from api.task_registry import TaskRegistry

        assert await TaskRegistry().stop("corpus_search") is False

    @pytest.mark.asyncio
    async def test_join_on_an_unknown_name_is_a_no_op(self):
        from api.task_registry import TaskRegistry

        await asyncio.wait_for(TaskRegistry().join("never-started"), timeout=1.0)


# ── portal selection (MAJOR-CHANGE/06) ───────────────────────────────────────


class TestPortalResolution:
    """The three precedence arms and their failure modes. The ids reaching the scraper must be
    a real subset of the catalog: a toggle that maps to nothing is the invisible-filter bug class
    again."""

    def _resolve(self, explicit, cfg):
        from api.routers.discovery import _resolve_portals

        class Corpus:
            async def scrape_sources(self_inner):
                return {"sources": [
                    {"id": "arbeitnow", "default_enabled": True},
                    {"id": "jobicy", "default_enabled": True},
                    {"id": "themuse", "default_enabled": False},
                ]}

        return asyncio.run(_resolve_portals(Corpus(), cfg, explicit))

    def test_explicit_list_wins_and_is_sorted(self):
        portals, err = self._resolve(["jobicy", "arbeitnow"], {})
        assert err is None and portals == ["arbeitnow", "jobicy"]

    def test_unknown_id_is_refused_not_dropped(self):
        # Silence here would let a renamed portal vanish from the scrape while the UI still
        # shows its toggle on.
        portals, err = self._resolve(["arbeitnow", "ghostboard"], {})
        assert portals is None and "ghostboard" in err

    def test_all_off_selection_is_an_error(self):
        # A zero-source scrape is indistinguishable from a broken one — reject at the API, the
        # same rule the UI enforces client-side.
        portals, err = self._resolve([], {})
        assert portals is None and "at least one portal" in err

    def test_saved_map_selects_the_truthy_ids(self):
        cfg = {"scrape_portals": json.dumps({"arbeitnow": True, "themuse": False, "jobicy": False})}
        portals, err = self._resolve(None, cfg)
        assert err is None and portals == ["arbeitnow"]

    def test_saved_map_missing_keys_enable_new_selectable_portals(self):
        # A map written when the catalog was smaller (or a hand-written partial) does not vote
        # on the portals it never mentions — the catalog default does. Without this a stale
        # partial map silently narrows every search while the UI shows the defaults as on.
        cfg = {"scrape_portals": json.dumps({"arbeitnow": True})}
        portals, err = self._resolve(None, cfg)
        assert err is None and portals == ["arbeitnow", "jobicy", "themuse"]

    def test_saved_map_stale_keys_are_ignored_not_fatal(self):
        cfg = {"scrape_portals": json.dumps({"arbeitnow": True, "ghostboard": True})}
        portals, err = self._resolve(None, cfg)
        assert err is None and portals == ["arbeitnow", "jobicy", "themuse"]

    def test_no_saved_map_uses_all_selectable_portals(self):
        # Resolved to an explicit list rather than None: the run must RECORD the set it covered,
        # or the superset freshness rule has nothing to compare.
        portals, err = self._resolve(None, {})
        assert err is None and portals == ["arbeitnow", "jobicy", "themuse"]

    def test_empty_saved_map_uses_all_selectable_portals(self):
        portals, err = self._resolve(None, {"scrape_portals": "{}"})
        assert err is None and portals == ["arbeitnow", "jobicy", "themuse"]

    def test_corrupt_saved_map_says_so(self):
        portals, err = self._resolve(None, {"scrape_portals": "not-json{"})
        assert portals is None and "corrupt" in err

    def test_dead_catalog_passes_an_explicit_list_through(self):
        # The scraper validates ids hard; refusing to invent a selection from an empty catalog is
        # the honest degradation.
        from api.routers.discovery import _resolve_portals

        class DeadCorpus:
            async def scrape_sources(self):
                return {"sources": []}

        portals, err = asyncio.run(_resolve_portals(DeadCorpus(), {}, ["arbeitnow"]))
        assert err is None and portals == ["arbeitnow"]


class TestScrapeCarriesTheSelection:
    def test_service_forwards_portals_to_the_corpus(self):
        client = FakeClient()
        service = CorpusDiscoveryService(client)
        asyncio.run(service.scrape_start("swe", 24, "India", ["arbeitnow"]))
        call = client.scrape_start_calls[-1]
        assert call["portals"] == ["arbeitnow"] and call["hours"] == 24

    def test_freshness_ask_includes_the_selection(self):
        # phrase+location alone would short-circuit a 40-portal search with a 3-portal run.
        client = FakeClient()
        service = CorpusDiscoveryService(client)
        fresh = asyncio.run(service.scrape_fresh("swe", "India", ["arbeitnow", "jobicy"]))
        assert fresh is False
        assert client.scrape_fresh_calls[-1]["portals"] == ["arbeitnow", "jobicy"]


def test_search_leaves_recency_unwindowed_by_default():
    # 2026-09 ruling: freshness is a signal, not a filter. Every match is served with its
    # posting date shown; fresh rows badge "<24h" and rank up, nothing is hidden for age.
    # Callers that want a hard window still pass fresh_hours explicitly.
    client = FakeClient(jobs=[])
    service = CorpusDiscoveryService(client)
    asyncio.run(service.search(query="swe", profile=None, settings=None, rerank=False))
    assert client.search_calls[-1]["fresh_hours"] is None
