"""Focused task-manager state contract for duplicate review handoff."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_review_required_freezes_completion_releases_worker_and_persists_duplicates(tmp_path):
    from api.ingestion_tasks import IngestionTaskManager
    from data.sqlite.connection import init_sql
    from data.sqlite.ingestion_tasks import get_active_task_record, get_latest_task_record

    db_path = str(tmp_path / "ingestion-review.db")
    init_sql(db_path)
    manager = IngestionTaskManager(db_path=db_path)
    task = await manager.create_task(
        filename="resume.pdf",
        file_path="/tmp/resume.pdf",
        tag_id=None,
        topic="resume",
    )

    # Simulate the manager's active worker bookkeeping.  Review handoff must
    # release both references before returning to the caller.
    manager._asyncio_tasks[task.task_id] = object()
    duplicate_payload = [
        {
            "pair_id": "pair-1",
            "parent_kind": "experience",
            "existing_point_id": "point-old",
            "existing_text": "Built the old pipeline.",
            "new_text": "Built the revised pipeline.",
            "status": "pending",
        }
    ]
    frozen_at = "2026-09-09T10:00:00+00:00"

    review = await manager.update_task(
        task.task_id,
        status="review_required",
        stage="deduping",
        stage_number=3,
        stage_message="Review duplicate points.",
        duplicates=duplicate_payload,
        completed_at=frozen_at,
    )

    assert review is not None
    assert review.status == "review_required"
    assert review.completed_at == frozen_at
    assert review.duplicates == duplicate_payload
    assert review.to_dict()["reviewable_duplicates"] == duplicate_payload
    assert manager._active_task_id is None
    assert task.task_id not in manager._asyncio_tasks

    # A later status/thought update must not move the frozen completion time or
    # erase the durable review payload.
    later = await manager.update_task(
        task.task_id,
        status="review_required",
        thoughts=["Waiting for duplicate decisions."],
        completed_at="2026-09-09T11:00:00+00:00",
    )
    assert later is not None
    assert later.completed_at == frozen_at
    assert later.duplicates == duplicate_payload

    # The status endpoint's persisted/latest path must return the review data,
    # while the active-worker query must not treat it as running.
    assert get_active_task_record(db_path=db_path) is None
    stored = get_latest_task_record(db_path=db_path)
    assert stored is not None
    assert stored["status"] == "review_required"
    assert stored["completed_at"] == frozen_at
    assert stored["duplicates"] == duplicate_payload

    hydrated_manager = IngestionTaskManager(db_path=db_path)
    hydrated = await hydrated_manager.get_task(task.task_id)
    assert hydrated is not None
    assert hydrated.status == "review_required"
    assert hydrated.completed_at == frozen_at
    assert hydrated.duplicates == duplicate_payload
