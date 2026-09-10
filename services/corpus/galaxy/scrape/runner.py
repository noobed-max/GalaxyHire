"""Start, watch and stop a scrape, with state that survives a page refresh.

## Why the corpus owns this

The scraper is a one-shot Node CLI, not a service. Something has to spawn it, and that something
has to remember what it started. The corpus is the right owner because it is the process every
scraped job is posted *into* — so it can count arrivals authoritatively without the scraper
reporting anything, and it already has a database to keep the state in.

The alternative, spawning from `apps/api` and holding state in memory, fails the actual requirement:
"refreshing the page should be able to dynamically detect the state of scraper etc like save
states". In-memory state dies with the process and React state dies with the tab.

## Why progress is derived, not reported

The scraper prints its summary once, at the end. A `progress` column fed from its stdout would sit
at zero for ten minutes and then jump to done, which is worse than no progress bar because it looks
stuck. So `jobs_before` is recorded at start and progress is computed live as
`count(canonical_jobs) - jobs_before`. That number is a fact about the database rather than a claim
by a subprocess, and it keeps working if the scraper is killed, crashes, or is replaced.

## What reaches the boards

The phrase, the location, and the selected portal set — nothing else. Seniority, negatives and
years are filters applied afterwards over stored jobs (ARCHITECTURE.md D7 + its title-gate
amendment, MAJOR-CHANGE/05 §4). Sending more would mean re-scraping every time the user changed
their mind, and hitting every board once per filter change rather than once per role.

## Freshness is keyed on phrase + location + portals

A completed scrape means "this exact question was recently asked". A previous run that scraped a
SUBSET of the requested portals is not an answer to the wider question — serving it would show
the user 3 boards' results under a 40-board search with nothing on screen to say so. Supersets
are accepted; subsets are not.

Only runs that finished with ``done`` count as fresh. A ``stopped`` run was interrupted by the
user, so it covered an unknown subset of its recorded portals: treating it as fresh would let a
partial snapshot suppress the collection that would have completed it for the next 24 hours.

## The native Python-connector path is gone

This file used to also fan out to the GalaxyJobsAi Python adapters (`PYTHON_SITES`,
`_run_python_boards`). Those connectors were deleted with the move to career-ops-only scraping;
the Node worker is now the only thing this spawns.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker

log = structlog.get_logger(__name__)

#: Where the Node scraper lives, relative to this service.
SCRAPER_DIR = Path(__file__).resolve().parents[3] / "scraper-node"

#: A run older than this with no completion is treated as dead. Covers the case where the corpus
#: was killed mid-scrape: the row would otherwise say "running" forever and block every new scrape.
STALE_AFTER_SECONDS = 60 * 60 * 3


def _resolve_npm() -> str | None:
    """Find npm even when the corpus was started outside an interactive shell.

    nvm, Volta, mise and asdf commonly add npm only from shell startup files.
    Desktop launchers and service managers do not read those files, so relying
    exclusively on ``PATH`` makes the scraper work in a terminal but fail from
    the application.  An explicit ``SCRAPER_NPM`` always wins.
    """
    override = str(os.environ.get("SCRAPER_NPM") or "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved or (override if Path(override).is_file() else None)

    resolved = shutil.which("npm")
    if resolved:
        return resolved

    home = Path.home()
    candidates = [
        Path(str(os.environ.get("NVM_BIN") or "")) / "npm",
        Path(str(os.environ.get("VOLTA_HOME") or "")) / "bin" / "npm",
        home / ".volta" / "bin" / "npm",
    ]
    for pattern in (
        ".nvm/versions/node/*/bin/npm",
        ".local/share/mise/installs/node/*/bin/npm",
        ".asdf/installs/nodejs/*/bin/npm",
    ):
        candidates.extend(sorted(home.glob(pattern), reverse=True))

    for candidate in candidates:
        if str(candidate) not in {"npm", "bin/npm"} and candidate.is_file():
            return str(candidate)
    return None


@dataclass
class ScrapeState:
    """Everything the UI needs to render a scrape, live or finished."""

    running: bool = False
    phrase: str = ""
    status: str = "idle"          # idle | running | done | failed | stopped
    collected: int = 0            # jobs added since this run started
    elapsed_seconds: float = 0.0
    error: str = ""
    run_id: int | None = None
    portals: tuple[str, ...] = field(default_factory=tuple)
    # True only when this caller asked to start a run while another persisted run was already
    # active.  Keeping the run state alongside the flag lets API callers return an honest 409
    # without changing the normal refresh/status contract.
    conflict: bool = False

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "phrase": self.phrase,
            "status": self.status,
            "collected": self.collected,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "error": self.error,
            "run_id": self.run_id,
            "portals": list(self.portals),
            "conflict": self.conflict,
        }


async def _job_count() -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        return int((await s.execute(text("SELECT count(*) FROM canonical_jobs"))).scalar_one())


async def _reap_stale() -> None:
    """Mark abandoned runs failed so they stop blocking new ones.

    A row says "running" until something marks it otherwise. If the corpus is killed mid-scrape
    nothing ever does, and every later scrape is refused as "already running" — a permanently stuck
    product from a single crash.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE scrape_runs SET status = 'failed', finished_at = now(), "
                "error = 'abandoned — the corpus restarted while this run was in flight' "
                "WHERE status = 'running' "
                "AND started_at < now() - make_interval(secs => :secs)"
            ),
            {"secs": STALE_AFTER_SECONDS},
        )
        await s.commit()


def _alive(pid: int | None) -> bool:
    """Whether the scrape is still running — checked across the whole **process group**.

    `npm start` is a wrapper that spawns `tsx`, which does the actual work, so the recorded pid is
    npm's; if npm ever exits while its children continue, a plain `os.kill(pid, 0)` would report
    dead and the run would be finalised early — frozen count, no error, jobs still arriving behind
    it. Since the process is started with `start_new_session=True`, every descendant shares a
    group whose id is that pid, so signal 0 to the negative pid asks "is any part of this scrape
    still alive" instead of "is the wrapper".
    """
    if not pid:
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group exists but is not ours to signal. Existing is what matters here, so this counts
        # as alive — the opposite reading is what produced the premature-completion bug.
        return True
    return True


async def _reconcile() -> None:
    """Finalise runs whose process is gone but which nobody was left to mark finished.

    Restarting the corpus kills the `_watch` task, while the scrape itself survives — it is started
    in its own session on purpose, so stopping the API does not abandon a ten-minute collection
    half-done. Checking the process group on every status poll closes the gap: it is a couple of
    syscalls, it needs no background task to have survived, and it heals whether the corpus
    restarted once or ten times.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, pid, EXTRACT(EPOCH FROM (now() - started_at)) AS age_s "
                    "FROM scrape_runs WHERE status = 'running'"
                )
            )
        ).mappings().all()
        for row in rows:
            if row["pid"] is None:
                # The pid is written back after Popen returns, so a brand-new row may
                # legitimately be pidless for a moment — but a row that has sat pidless for
                # minutes never spawned (its start() raised before exec). Reconciling those
                # as 'done' would manufacture a phantom completed run that then satisfies
                # the freshness key and short-circuits real searches for 24h.
                if (row["age_s"] or 0) > 600:
                    await s.execute(
                        text(
                            "UPDATE scrape_runs SET status = 'failed', finished_at = now(), "
                            "error = 'spawn never recorded a pid — the worker failed to start' "
                            "WHERE id = :id"
                        ),
                        {"id": row["id"]},
                    )
                continue
            if _alive(row["pid"]):
                continue
            await s.execute(
                text(
                    "UPDATE scrape_runs SET status = 'done', finished_at = now(), "
                    "jobs_after = (SELECT count(*) FROM canonical_jobs) WHERE id = :id"
                ),
                {"id": row["id"]},
            )
        if rows:
            await s.commit()


async def current() -> ScrapeState:
    """The latest run's state, read from the database so a refresh reattaches to it."""
    await _reap_stale()
    await _reconcile()
    sm = get_sessionmaker()
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, phrase, status, jobs_before, jobs_after, error, portals, "
                    "EXTRACT(EPOCH FROM (coalesce(finished_at, now()) - started_at)) AS elapsed "
                    "FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
                )
            )
        ).mappings().first()

    if row is None:
        return ScrapeState()

    running = row["status"] == "running"
    # While running, progress is live from the table. Once finished it is frozen, so a later
    # unrelated scrape cannot inflate a completed run's number.
    after = await _job_count() if running else (row["jobs_after"] or row["jobs_before"])
    return ScrapeState(
        running=running,
        phrase=row["phrase"],
        status=row["status"],
        collected=max(0, after - row["jobs_before"]),
        elapsed_seconds=float(row["elapsed"] or 0.0),
        error=row["error"] or "",
        run_id=row["id"],
        portals=tuple(row["portals"] or ()),
    )


#: How long a completed scrape of a phrase counts as fresh. Searching is now what triggers
#: collection, so without this every search would cost a full scrape run — including pressing
#: enter twice on the same phrase, or adjusting a filter. 24 matches the product's recency
#: window exactly: yesterday's fresh postings are by definition outside today's window, so
#: anything older than a day MUST be re-collected, not served from a stale verdict.
FRESH_FOR_HOURS = 24


def _portals_cover(requested: tuple[str, ...] | None, recorded: tuple[str, ...] | None) -> bool:
    """Whether a recorded run's portal set answers the requested one.

    Superset-or-equal answers it; subset does not (see module docstring). A recorded NULL predates
    portal tracking and cannot vouch for any set — after the [02] purge there are no such rows,
    and the conservative reading keeps a restored backup from silently short-circuiting scrapes.
    """
    if not requested:  # caller wants the config defaults — any recorded set that ran them is fine,
        return bool(recorded)  # and `start()` records the resolved set, so equality holds anyway
    if not recorded:
        return False
    return set(requested).issubset(set(recorded))


async def freshly_scraped(
    phrase: str,
    *,
    location: str = "",
    portals: tuple[str, ...] | None = None,
    within_hours: int = FRESH_FOR_HOURS,
) -> bool:
    """Whether this exact question — phrase, location, AND portal set — was asked recently.

    Location is part of the key: "software engineer" collected for India is not fresh for the UK.
    Portals are part of the key for the same reason in the other direction: a run over 3 boards is
    not fresh for a search across 40 (MAJOR-CHANGE/06 §5).

    Only a ``done`` run answers the question. A ``stopped`` run is a partial snapshot of an
    unknown subset of its portals — however many it recorded — so it can never vouch for the
    requested coverage; the next search must collect the rest.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT portals FROM scrape_runs "
                    "WHERE lower(phrase) = lower(:p) AND lower(coalesce(location,'')) = lower(:loc) "
                    "AND status = 'done' "
                    "AND finished_at > now() - make_interval(hours => :h)"
                ),
                {"p": phrase.strip(), "loc": (location or "").strip(), "h": within_hours},
            )
        ).mappings().all()
    return any(_portals_cover(portals, tuple(r["portals"] or ())) for r in rows)


async def start(
    phrase: str,
    *,
    hours: int = FRESH_FOR_HOURS,
    location: str = "",
    portals: list[str] | None = None,
) -> ScrapeState:
    """Spawn a scrape for `phrase` in `location` across `portals`. Refuses if one is in flight.

    `portals` is the resolved UI selection (the app resolves "saved selection vs explicit" before
    calling, so the scraper sees one truth). Absent means "config defaults": the default-enabled
    set is resolved HERE — from the same catalog the UI lists — and recorded on the row, so the
    freshness key compares what actually ran rather than a NULL that vouches for nothing.
    """
    phrase = (phrase or "").strip()
    location = (location or "").strip()
    if not phrase:
        raise ValueError("a scrape needs a phrase")

    if not portals:
        # Same default set the worker applies (gh.toggle) and the catalog advertises
        # (default_enabled): resolve it now so the recorded set, the spawned --portals, and the
        # next freshness check all see one truth.
        from galaxy.scrape.catalog import load_catalog

        # `off` can never run (the worker skips it even when named explicitly), so it must
        # not be recorded as covered — otherwise the freshness key would serve "coverage" that
        # never scraped.
        portals = sorted(
            s["id"] for s in load_catalog()
            if s["default_enabled"] and s["recency_policy"] != "off"
        )

    state = await current()
    if state.running:
        # Two scrapes of the same corpus duplicate effort and double the load on every board for no
        # extra coverage. Return the existing persisted state with an explicit conflict marker so
        # a dashboard Find action can tell the user why no second scraper was launched. Callers
        # that merely poll/attach can ignore the marker and keep showing this run.
        return ScrapeState(**{**state.__dict__, "conflict": True})

    before = await _job_count()
    sm = get_sessionmaker()
    async with sm() as s:
        run_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO scrape_runs (phrase, hours, location, portals, jobs_before) "
                        "VALUES (:p, :h, :loc, :portals, :b) RETURNING id"
                    ),
                    {
                        "p": phrase, "h": hours, "loc": location, "b": before,
                        "portals": list(portals) if portals else None,
                    },
                )
            ).scalar_one()
        )
        await s.commit()

    # Location IS passed to the boards, unlike every other filter. Boards that support it answer
    # far better for it; seniority, negatives and years stay post-hoc (D7, narrowed to
    # "location + role phrase" — the role reaches boards via src/query-injection.ts, location via
    # the source's own hint where it exists at all).
    node_args = ["--role", phrase, "--hours", str(hours)]
    if location:
        node_args += ["--location", location]
    if portals:
        node_args += ["--portals", ",".join(portals)]

    # npm lives wherever the operator's Node toolchain is (nvm shims it into interactive
    # shells only — systemd units do not see it). Resolve it now so a missing binary fails
    # HERE with the reason on the run row, instead of leaving a pidless 'running' row that
    # reconciliation would later bless as a phantom 'done'.
    npm = _resolve_npm()
    if not npm:
        err = (
            "cannot start the scraper: npm was not found. Install Node.js/npm or set "
            "SCRAPER_NPM to the npm executable before starting GalaxyHire."
        )
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE scrape_runs SET status = 'failed', finished_at = now(), "
                    "error = :err WHERE id = :id"
                ),
                {"err": err[:4000], "id": run_id},
            )
            await s.commit()
        raise ValueError(err)

    try:
        child_env = {
            **os.environ,
            "CORPUS_API_KEY": os.environ.get("CORPUS_API_KEY", "dev-key"),
            # npm installed by nvm/mise uses ``env node``. Put its directory
            # first so it launches the matching Node version instead of an old
            # system binary that happened to be on the service PATH.
            "PATH": str(Path(npm).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        proc = await asyncio.create_subprocess_exec(
            npm, "start", "--silent", "--", *node_args,
            cwd=str(SCRAPER_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Its own process group, so stopping kills the whole npm → node tree rather than just the
            # npm wrapper, which would leave the scraper running and untracked.
            start_new_session=True,
            env=child_env,
        )
    except OSError as exc:
        # Spawn itself failed (missing cwd, resource limits, …) — same visibility rule: the
        # row must say failed with the reason, never linger as a 'done' that never ran.
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE scrape_runs SET status = 'failed', finished_at = now(), "
                    "error = :err WHERE id = :id"
                ),
                {"err": f"scraper spawn failed: {exc}"[:4000], "id": run_id},
            )
            await s.commit()
        raise ValueError(f"scraper spawn failed: {exc}") from exc

    async with sm() as s:
        await s.execute(
            text("UPDATE scrape_runs SET pid = :pid WHERE id = :id"),
            {"pid": proc.pid, "id": run_id},
        )
        await s.commit()

    # Watch it in the background: the caller gets immediate state, and completion is recorded even
    # if nobody is polling.
    asyncio.create_task(_watch(proc, run_id))  # noqa: RUF006 - fire and forget by design
    return await current()


async def _finalize_maintenance() -> None:
    """Reconcile the corpus after a scrape: global dedup, then ensure everything is embedded.

    The scraper already embeds its own batches; this pass catches what slipped between batches —
    near-duplicates whose canonical ids only merge ACROSS runs (the in-batch DedupEngine can't see
    a job scraped on a different day), and rows orphaned by a partial failure. Order matters:
    dedup nulls merged survivors' vectors, so embed must run after. Both are idempotent, so
    running them on a clean corpus costs two queries.
    """
    try:
        from galaxy.worker.tasks import dedup_global, embed_new

        d = await dedup_global()
        log.info("scrape.global_dedup", **d)
        # Merge debris: a canonical row with no sightings is not a job. The merge re-points
        # observations before deleting losers, so a crash between the two strands the loser
        # row; search refuses to serve such rows, and this sweep removes them.
        swept = await _sweep_sightless()
        if swept:
            log.info("scrape.sightless_swept", removed=swept)
        e = await embed_new()
        log.info("scrape.embed_sweep", **e)
    except Exception as exc:  # noqa: BLE001 — maintenance must not turn a done run into a failed one
        log.warning("scrape.finalize_maintenance_failed", error=str(exc))


async def _sweep_sightless() -> int:
    """Delete canonical rows no observation points at. Idempotent; returns the count removed."""
    sm = get_sessionmaker()
    async with sm() as s:
        result = await s.execute(
            text(
                "DELETE FROM canonical_jobs c WHERE NOT EXISTS "
                "(SELECT 1 FROM source_observations o "
                "WHERE o.canonical_job_id = c.canonical_job_id)"
            )
        )
        await s.commit()
        return int(result.rowcount or 0)


async def _watch(proc: asyncio.subprocess.Process, run_id: int) -> None:
    """Wait for the scrape to exit, finalize the corpus, and record how it went."""
    stdout, _ = await proc.communicate()
    tail = (stdout or b"").decode("utf-8", "replace")[-2000:]

    if proc.returncode == 0:
        await _finalize_maintenance()

    sm = get_sessionmaker()
    async with sm() as s:
        # Don't overwrite a deliberate stop with 'failed'; a killed process exits non-zero, and the
        # user knowing they stopped it is more useful than being told it broke.
        status_row = (
            await s.execute(text("SELECT status FROM scrape_runs WHERE id = :id"), {"id": run_id})
        ).scalar_one_or_none()
        if status_row == "stopped":
            final, error = "stopped", ""
        elif proc.returncode == 0:
            final, error = "done", ""
        else:
            final, error = "failed", f"scraper exited {proc.returncode}\n{tail}"

        await s.execute(
            text(
                "UPDATE scrape_runs SET status = :st, error = :err, finished_at = now(), "
                "jobs_after = (SELECT count(*) FROM canonical_jobs) WHERE id = :id"
            ),
            {"st": final, "err": error[:4000], "id": run_id},
        )
        await s.commit()


async def stop() -> bool:
    """Signal the running scrape to stop. Returns False when there is nothing running."""
    sm = get_sessionmaker()
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT id, pid FROM scrape_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
            )
        ).mappings().first()
        if row is None:
            return False

        # Marked before signalling, so `_watch` sees the intent and reports 'stopped' rather than
        # 'failed' when the non-zero exit arrives.
        await s.execute(
            text("UPDATE scrape_runs SET status = 'stopped', finished_at = now(), "
                 "jobs_after = (SELECT count(*) FROM canonical_jobs) WHERE id = :id"),
            {"id": row["id"]},
        )
        await s.commit()

    pid = row["pid"]
    if pid:
        try:
            # Negative pid signals the whole process group — npm spawns node as a child, and killing
            # only npm would orphan the scraper still holding connections to job boards.
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # Already gone, or not ours to signal. The row is updated either way, which is what the
            # UI reads; a stale pid must not turn a stop into an error.
            pass
    return True


async def clear_history() -> dict:
    """Stop collection and remove saved run metadata without deleting the reusable job corpus.

    Search phrases are user-authored data. The app's danger-zone reset therefore has to clear them
    even though canonical jobs are intentionally retained—the corpus is expensive shared product
    data, while ``scrape_runs`` is merely activity/freshness history.
    """
    stopped = await stop()
    sm = get_sessionmaker()
    async with sm() as s:
        result = await s.execute(text("DELETE FROM scrape_runs"))
        await s.commit()
    return {"cleared": max(0, int(result.rowcount or 0)), "stopped": stopped}
