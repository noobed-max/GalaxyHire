"""HTTP client for the corpus service.

The corpus (`services/corpus`) owns job storage and retrieval; this app owns the profile, the
ranking explanation, the pipeline, and the graph. That split is decision D1/D2, and this module is
the only place the boundary is crossed — nothing else in the app should know the corpus is remote.

Every call fails soft. The corpus being down should degrade search to "no results, here's why",
not 500 the dashboard, because the rest of the app (profile, pipeline, graph) works fine without
it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from core.logging import get_logger

_log = get_logger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8100"
# No deadline. Two calls here wait on genuinely open-ended work: search re-ranks up to 200 jobs
# (~44s measured, already past the old 30s ceiling), and a search now waits for collection to
# finish. Any number chosen here is a guess about how long a scrape "should" take, and the first
# run that is slower than the guess fails for no reason. Requests are cancelled by the caller
# stopping the search, not by a stopwatch.
DEFAULT_TIMEOUT_S = None


class CorpusUnavailable(RuntimeError):
    """The corpus could not be reached or refused the request."""


@dataclass
class CorpusConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("GALAXYHIRE_CORPUS_URL", DEFAULT_BASE_URL))
    api_key: str = field(default_factory=lambda: os.environ.get("GALAXYHIRE_CORPUS_API_KEY", "dev-key"))
    #: None means no deadline. Override with GALAXYHIRE_CORPUS_TIMEOUT_S when you want one.
    timeout_s: float | None = field(
        default_factory=lambda: (
            float(os.environ["GALAXYHIRE_CORPUS_TIMEOUT_S"])
            if os.environ.get("GALAXYHIRE_CORPUS_TIMEOUT_S")
            else DEFAULT_TIMEOUT_S
        )
    )

    def normalized_base(self) -> str:
        return self.base_url.rstrip("/")


class CorpusClient:
    def __init__(self, config: CorpusConfig | None = None):
        self.config = config or CorpusConfig()

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.config.api_key, "content-type": "application/json"}

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.config.normalized_base()}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                res = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CorpusUnavailable(f"corpus unreachable at {self.config.normalized_base()}: {exc}") from exc
        if res.status_code >= 400:
            raise CorpusUnavailable(f"corpus returned {res.status_code} for {path}: {res.text[:200]}")
        # 204 is a success with no body, and `.json()` on an empty body raises JSONDecodeError —
        # which surfaced as a 500 *after* the write had already committed, the worst shape of
        # error: the caller is told it failed, retries, and duplicates the record.
        if res.status_code == 204 or not res.content:
            return {}
        return res.json()

    async def _get(self, path: str) -> dict:
        url = f"{self.config.normalized_base()}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                res = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CorpusUnavailable(f"corpus unreachable at {self.config.normalized_base()}: {exc}") from exc
        if res.status_code >= 400:
            raise CorpusUnavailable(f"corpus returned {res.status_code} for {path}: {res.text[:200]}")
        if res.status_code == 204 or not res.content:
            return {}
        return res.json()

    async def search(
        self,
        *,
        search_term: str | None = None,
        positive_skills: list[str] | None = None,
        negative_titles: list[str] | None = None,
        negative_phrases: list[str] | None = None,
        max_years: int | None = None,
        max_seniority: str | None = None,
        remote: bool | None = None,
        location: str | None = None,
        limit: int = 50,
        sort: str = "relevance",
        fresh_hours: int | None = None,
        observed_after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve candidates from the stored corpus.

        These are *filters over already-collected jobs*, never scrape parameters — see D7. The
        corpus was populated by a broad role-level scrape; narrowing happens here. `fresh_hours`
        is the product recency window (R3): jobs must either prove a post date inside it or have
        landed in the corpus inside it.
        """
        payload: dict[str, Any] = {
            "search_term": search_term,
            "positive_skills": positive_skills or [],
            "negative_titles": negative_titles or [],
            # Carries the "no SDE 2 / no 3+ years" half of a plain-English brief. Without it the
            # parser's negatives were computed and then dropped on the floor, so asking to
            # exclude something did nothing at all.
            "negative_phrases": negative_phrases or [],
            "max_years": max_years,
            "max_seniority": max_seniority,
            "remote": remote,
            "location": location,
            "limit": limit,
            "sort": sort,
            "fresh_hours": fresh_hours,
            # When a user explicitly starts a new collection, restrict retrieval to canonical
            # jobs observed by that run so older corpus rows cannot masquerade as fresh results.
            "observed_after": observed_after,
        }
        body = await self._post("/search", payload)
        return body.get("results", [])

    async def parse_query(self, text: str) -> dict[str, Any]:
        """Compile plain English into filter fields. Never raises on a bad parse — the corpus
        falls back to a deterministic parse so search can't 502 on a phrasing it dislikes."""
        return await self._post("/search/parse", {"text": text})

    async def get_job(self, canonical_job_id: str) -> dict[str, Any] | None:
        try:
            return await self._get(f"/jobs/{canonical_job_id}")
        except CorpusUnavailable as exc:
            if "404" in str(exc):
                return None
            raise

    async def prepare_application(
        self, job_id: str, *, check_live: bool = True, output_dir: str | None = None
    ) -> dict[str, Any]:
        """Tailor, render the résumé to disk, and persist the per-job fill context (§6).

        A 409 from the corpus means the posting closed. That is returned as `{"expired": True}`
        rather than raised, because it is a normal thing to discover at apply time and the caller
        needs to tell the user, not handle an exception.
        """
        payload = {"job_id": job_id, "check_live": check_live, "output_dir": output_dir}
        try:
            return await self._post("/applications/prepare", payload)
        except CorpusUnavailable as exc:
            if "409" in str(exc):
                return {"expired": True}
            raise

    async def fill_context(self, url: str | None) -> dict[str, Any] | None:
        """The prepared fill context matching a portal URL, or None."""
        suffix = f"?url={quote(url, safe='')}" if url else ""
        try:
            return await self._get(f"/applications/fill-context{suffix}")
        except CorpusUnavailable as exc:
            if "404" in str(exc):
                return None
            raise

    async def asset(self, ref: str) -> tuple[bytes, str]:
        """Fetch a generated corpus asset through the authenticated service boundary."""
        if not ref or not ref.isalnum():
            raise CorpusUnavailable("invalid corpus asset reference")
        url = f"{self.config.normalized_base()}/assets/{ref}"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                res = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CorpusUnavailable(f"corpus unreachable fetching asset: {exc}") from exc
        if res.status_code >= 400:
            raise CorpusUnavailable(f"corpus returned {res.status_code} for asset {ref}")
        return res.content, res.headers.get("content-type", "application/octet-stream")

    async def render_latex(self, job_id: str) -> str | None:
        """LaTeX source for a job's tailored résumé (§5).

        Returns the source itself rather than an asset reference: the UI puts it straight into an
        editor, so a second round trip to dereference a ref would buy nothing.
        """
        body = await self._post("/tailor/render", {"job_id": job_id, "format": "tex", "kind": "resume"})
        ref = body.get("asset_ref")
        if not ref:
            return None
        url = f"{self.config.normalized_base()}/assets/{ref}"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                res = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CorpusUnavailable(f"corpus unreachable fetching asset: {exc}") from exc
        if res.status_code >= 400:
            raise CorpusUnavailable(f"corpus returned {res.status_code} for asset {ref}")
        return res.text

    async def compile_latex(self, source: str) -> dict[str, Any]:
        """Compile edited LaTeX. Returns `{ok, pdf_base64}` or `{ok: False, error, log}`.

        A compile failure is an expected outcome of editing, so it comes back as data rather than
        raising — the UI needs to render the log, not an exception.
        """
        import base64

        url = f"{self.config.normalized_base()}/tailor/compile"
        try:
            timeout = 90.0 if self.config.timeout_s is None else max(self.config.timeout_s, 90.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(url, json={"source": source}, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CorpusUnavailable(f"corpus unreachable compiling latex: {exc}") from exc
        if res.status_code == 200:
            return {"ok": True, "pdf_base64": base64.b64encode(res.content).decode("ascii")}
        try:
            return {"ok": False, **res.json()}
        except ValueError:
            return {"ok": False, "error": f"corpus returned {res.status_code}"}

    async def compile_available(self) -> dict[str, Any]:
        """Whether a local LaTeX engine exists, so the UI can hide preview rather than fail at it."""
        try:
            return await self._get("/tailor/compile/available")
        except CorpusUnavailable:
            return {"available": False, "engine": None}

    async def stats(self) -> dict[str, Any]:
        """Corpus size and how much of it is searchable.

        `searchable` < `canonical_jobs` means jobs are ingested but not yet embedded, which is the
        normal state right after a scrape. Surfacing both is what stops "no results" from being
        mistaken for "no jobs".
        """
        return await self._get("/ingest/count")

    async def record_feedback(self, canonical_job_id: str, signal: str) -> None:
        """Tell the corpus what the user thought of a result.

        Fire-and-forget from the caller's point of view, but *not* swallowed here: the corpus
        validates the signal name, and a rejected signal must surface rather than leaving the user
        clicking a button that records nothing.
        """
        await self._post("/feedback", {"canonical_job_id": canonical_job_id, "signal": signal})

    async def locations(self, q: str) -> list[dict[str, Any]]:
        """Location suggestions drawn from jobs that actually exist in the corpus."""
        body = await self._get(f"/locations?q={q}")
        return body.get("suggestions", [])

    async def scrape_start(
        self,
        phrase: str,
        hours: int = 24,
        location: str = "",
        portals: list[str] | None = None,
    ) -> dict[str, Any]:
        """Begin collecting `phrase` in `location` across `portals`.

        Location goes to the boards; every other filter stays post-hoc. Portals is the resolved
        UI selection — the boards that actually get asked — and the corpus records it on the run
        so freshness can't short-circuit a wider question with a narrower answer.
        """
        return await self._post(
            "/scrape/start",
            {"phrase": phrase, "hours": hours, "location": location, "portals": portals},
        )

    async def scrape_fresh(
        self, phrase: str, location: str = "", portals: list[str] | None = None
    ) -> bool:
        """Has this exact question — phrase, location AND portal set — been asked recently?"""
        from urllib.parse import quote

        qs = f"phrase={quote(phrase)}&location={quote(location)}"
        if portals:
            qs += f"&portals={quote(','.join(portals))}"
        body = await self._get(f"/scrape/fresh?{qs}")
        return bool(body.get("fresh"))

    async def scrape_sources(self) -> list[dict[str, Any]]:
        """The portal catalog from the scraper's own config — what CAN be scraped."""
        body = await self._get("/scrape/sources")
        return body.get("sources", [])

    async def scrape_status(self) -> dict[str, Any]:
        """Live scrape state, read from the corpus database so a refresh reattaches to it."""
        return await self._get("/scrape/status")

    async def scrape_stop(self) -> dict[str, Any]:
        return await self._post("/scrape/stop", {})

    async def scrape_clear_history(self) -> dict[str, Any]:
        """Remove persisted user-authored scrape phrases, but keep canonical jobs."""
        return await self._post("/scrape/history/clear", {})

    async def preference(self) -> dict[str, Any]:
        """What accumulated feedback currently does to this user's searches.

        Needed so the effect can be shown and reasoned about. A learned filter nobody can inspect
        is indistinguishable from the corpus having less in it.
        """
        return await self._get("/preference")


def create_corpus_client(config: CorpusConfig | None = None) -> CorpusClient:
    return CorpusClient(config)
