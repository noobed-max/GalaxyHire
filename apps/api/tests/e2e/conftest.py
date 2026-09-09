"""Pytest Fixtures and Storage Isolation Harness for GalaxyHire E2E Tests.

Configures ephemeral storage for real SQLite and real Kùzu graph, deterministic
FastAPI TestClient, and MockLLMEngine.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
import pytest
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests.e2e.mock_llm_engine import MockLLMEngine, install_mock_llm  # noqa: E402


@pytest.fixture
def e2e_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any]]:
    """Isolated environment with real SQLite and real Kùzu graph in ephemeral directory."""
    app_data = tmp_path / "appdata"
    app_data.mkdir(parents=True, exist_ok=True)

    # Point storage paths to isolated ephemeral directory
    monkeypatch.setenv("JHM_APP_DATA_DIR", str(app_data))
    monkeypatch.setenv("LOCALAPPDATA", str(app_data))

    # Stop persistent ingestion workers before closing their SQLite pool.
    from api.ingestion_tasks import reset_ingestion_task_manager
    reset_ingestion_task_manager()

    # Close any existing pooled connections from previous runs
    from data.sqlite.connection import _DB_POOL, init_sql
    _DB_POOL.close_all()

    # Initialize real SQLite schema and run all migrations (001 - 010)
    db_path = str(app_data / "crm.db")
    init_sql(db_path)

    for mod_name in (
        "data.sqlite.connection",
        "data.repository",
        "data.sqlite.point_tags",
        "data.sqlite.tags",
        "data.sqlite.documents",
        "data.sqlite.ingestion_tasks",
        "data.sqlite.settings",
        "data.sqlite.conflicts",
        "data.sqlite.doc_selections",
        "data.sqlite.leads",
        "data.sqlite.events",
        "data.sqlite.misc",
    ):
        try:
            monkeypatch.setattr(f"{mod_name}.DEFAULT_DB_PATH", db_path)
        except Exception:
            pass

    from data.sqlite.settings import save_settings
    save_settings({"embedding_provider": "hash"}, db_path=db_path)

    # Initialize real Kùzu graph schema
    from data.graph.connection import init_graph
    init_graph()

    # Reconnect LanceDB vector store to isolated ephemeral directory
    from data.vector.connection import refresh_vector_store
    refresh_vector_store()

    # Configure mock LLM engine
    mock_llm = install_mock_llm(monkeypatch)

    from api.rate_limit import reset_all_rate_limiters
    reset_all_rate_limiters()

    import main
    test_token = "e2e-auth-token-xyz987"
    monkeypatch.setattr(main, "_API_TOKEN", test_token)
    client = TestClient(main.app, base_url="http://127.0.0.1", raise_server_exceptions=True)
    auth_headers = {"Authorization": f"Bearer {test_token}"}

    from data.repository import create_repository
    repo = create_repository()

    context = {
        "tmp_path": tmp_path,
        "app_data": app_data,
        "db_path": db_path,
        "client": client,
        "auth": auth_headers,
        "mock_llm": mock_llm,
        "repo": repo,
    }

    try:
        yield context
    finally:
        reset_ingestion_task_manager()
        _DB_POOL.close_all()
        reset_all_rate_limiters()


@pytest.fixture
def sample_resume_text() -> str:
    """Standard sample plain-text resume content."""
    return (
        "Alex Mercer\n"
        "alex.mercer@example.com | (555) 019-2834 | San Francisco, CA\n"
        "github.com/example | linkedin.com/in/alex-mercer\n\n"
        "PROFESSIONAL SUMMARY\n"
        "Senior Software Engineer with 6+ years specializing in distributed systems and cloud infrastructure.\n\n"
        "EXPERIENCE\n"
        "CloudScale Inc - Senior Backend Engineer (Jan 2022 - Present)\n"
        "• Architected event-driven microservices processing 50k events/sec using Kafka.\n"
        "• Engineered zero-downtime database migration pipeline for 10TB PostgreSQL cluster.\n"
        "• Mentored 4 junior engineers on distributed systems architecture.\n\n"
        "TechCorp Solutions - Software Engineer (Jun 2019 - Dec 2021)\n"
        "• Developed RESTful APIs in Go and Python serving 2M daily active users.\n"
        "• Optimized redis caching layer reducing P99 latency by 45ms.\n\n"
        "PROJECTS\n"
        "Distributed Task Scheduler | Go, Raft, gRPC\n"
        "• Implemented consensus-based distributed job queue handling node failovers in <200ms.\n\n"
        "SKILLS\n"
        "Languages: Python, Go, SQL\n"
        "Frameworks & Tools: FastAPI, Kubernetes, Docker, Kafka, Redis, PostgreSQL\n"
    )


@pytest.fixture
def sample_variant_resume_text() -> str:
    """Resume with similar variant bullets to trigger reviewable duplicate staging."""
    return (
        "Alex Mercer\n"
        "alex.mercer@example.com | San Francisco, CA\n\n"
        "EXPERIENCE\n"
        "CloudScale Inc - Senior Backend Engineer (Jan 2022 - Present)\n"
        "• Architected event-driven microservices on AWS MSK Kafka cluster processing 50,000 events/sec.\n"
        "• Engineered zero-downtime database migration pipeline for 10TB PostgreSQL cluster.\n"
        "• Mentored junior engineers on distributed consensus protocols.\n"
    )


def create_resume_file(dir_path: Path, filename: str, content: str) -> Path:
    """Helper writing a text/fake PDF file to disk."""
    file_path = dir_path / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path


def poll_ingest_task(
    client: TestClient,
    auth: dict[str, str],
    task_id: str | None = None,
    timeout_seconds: float = 25.0,
    interval: float = 0.1,
) -> dict[str, Any]:
    """Helper polling GET /api/v1/documents/ingest/status until completed or failed."""
    url = "/api/v1/documents/ingest/status"
    if task_id:
        url += f"?task_id={task_id}"

    start_time = time.time()
    last_resp: dict[str, Any] | None = None

    while time.time() - start_time < timeout_seconds:
        res = client.get(url, headers=auth)
        if res.status_code == 200:
            data = res.json()
            last_resp = data
            if data.get("status") in ("completed", "failed", "cancelled", "review_required"):
                return data
        time.sleep(interval)

    if last_resp is not None:
        return last_resp
    raise TimeoutError(f"Polling task {task_id} timed out after {timeout_seconds}s")
