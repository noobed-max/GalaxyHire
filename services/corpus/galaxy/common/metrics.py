"""In-process ingestion metrics (docs/02 §5, docs/08 §5 block-rate visibility).

Per-source counters the pipeline updates on every fetch, exposed via /ingest/metrics so a
newly-defended source (rising block rate) is visible. In-memory for v1; a Prometheus/Redis
exporter slots in behind the same record_* calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass
class SourceMetric:
    runs: int = 0
    ok: int = 0
    failed: int = 0
    circuit_skips: int = 0
    jobs: int = 0
    last_ok: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None

    @property
    def block_rate(self) -> float:
        total = self.ok + self.failed
        return round(self.failed / total, 3) if total else 0.0


_metrics: dict[str, SourceMetric] = {}


def _m(site: str) -> SourceMetric:
    if site not in _metrics:
        _metrics[site] = SourceMetric()
    return _metrics[site]


def record_run(site: str, *, ok: bool, jobs: int = 0, error: str | None = None) -> None:
    m = _m(site)
    m.runs += 1
    if ok:
        m.ok += 1
        m.jobs += jobs
        m.last_ok = datetime.now(UTC).isoformat()
    else:
        m.failed += 1
        m.last_error = error
        m.last_error_at = datetime.now(UTC).isoformat()


def record_circuit_skip(site: str) -> None:
    _m(site).circuit_skips += 1


def snapshot() -> dict:
    return {
        "sources": {
            site: {**asdict(m), "block_rate": m.block_rate} for site, m in sorted(_metrics.items())
        },
        "total_jobs": sum(m.jobs for m in _metrics.values()),
    }


def reset() -> None:
    _metrics.clear()
