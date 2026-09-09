from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.logging import get_logger
from data.repository import Repository, create_repository

_log = get_logger(__name__)

TaskStatus = Literal["idle", "processing", "completed", "failed", "cancelled", "review_required"]
TaskStage = Literal["idle", "reading", "extracting", "deduping", "indexing", "completed", "failed"]


@dataclass
class IngestionTask:
    task_id: str
    filename: str
    file_path: str
    status: TaskStatus = "processing"
    tag_id: str | None = None
    topic: str = ""
    stage: TaskStage = "reading"
    stage_number: int = 1  # 1: reading, 2: extracting, 3: deduping, 4: indexing
    stage_message: str = "Reading document & extracting text..."
    progress_percent: int = 10
    thoughts: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    duplicates: list[dict] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        """Dynamically compute elapsed execution duration."""
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at) if self.completed_at else datetime.now(timezone.utc)
            return max(0.0, round((end - start).total_seconds(), 1))
        except Exception:
            return 0.0

    @property
    def has_staged_duplicates(self) -> bool:
        return len(self.duplicates) > 0 or self.status == "review_required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "filename": self.filename,
            "file_path": self.file_path,
            "tag_id": self.tag_id,
            "topic": self.topic,
            "stage": self.stage,
            "stage_number": self.stage_number,
            "stage_message": self.stage_message,
            "progress_percent": self.progress_percent,
            "elapsed_seconds": self.elapsed_seconds,
            "thoughts": list(self.thoughts),
            "has_staged_duplicates": self.has_staged_duplicates,
            "duplicates": self.duplicates,
            "reviewable_duplicates": self.duplicates,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> IngestionTask:
        thoughts = row.get("thoughts")
        if isinstance(thoughts, str):
            try:
                thoughts = json.loads(thoughts)
            except Exception:
                thoughts = []
        elif not isinstance(thoughts, list):
            thoughts = []

        duplicates = row.get("duplicates")
        if isinstance(duplicates, str):
            try:
                duplicates = json.loads(duplicates)
            except Exception:
                duplicates = []
        elif not isinstance(duplicates, list):
            duplicates = []

        result = row.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {}
        elif not isinstance(result, dict):
            result = {}

        return cls(
            task_id=row["task_id"],
            status=row.get("status", "processing"),
            filename=row.get("filename", ""),
            file_path=row.get("file_path", ""),
            tag_id=row.get("tag_id"),
            topic=row.get("topic", ""),
            stage=row.get("stage", "reading"),
            stage_number=int(row.get("stage_number", 1)),
            stage_message=row.get("stage_message", ""),
            progress_percent=int(row.get("progress_percent", 0)),
            thoughts=thoughts,
            error=row.get("error"),
            started_at=row.get("started_at") or datetime.now(timezone.utc).isoformat(),
            completed_at=row.get("completed_at"),
            duplicates=duplicates,
            result=result,
        )


class IngestionTaskManager:
    """Manages active and historical resume ingestion tasks with an in-memory
    L1 cache and asynchronous SQLite write-through persistence."""

    def __init__(self, repo: Repository | None = None, db_path: str | None = None) -> None:
        self._repo = repo or create_repository()
        self._db_path = db_path
        self._tasks: dict[str, IngestionTask] = {}
        self._active_task_id: str | None = None
        self._asyncio_tasks: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._bg_loop: asyncio.AbstractEventLoop | None = None
        self._bg_thread: threading.Thread | None = None

    def get_bg_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._bg_loop is None or not self._bg_loop.is_running():
                self._bg_loop = asyncio.new_event_loop()
                self._bg_thread = threading.Thread(
                    target=self._bg_loop.run_forever,
                    daemon=True,
                    name="ingest-bg-worker",
                )
                self._bg_thread.start()
            return self._bg_loop

    def spawn_worker(self, task_id: str, coro) -> concurrent.futures.Future:
        """Dispatches a coroutine to the persistent background loop and registers it."""
        loop = self.get_bg_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        self.register_asyncio_task(task_id, future)
        return future

    async def create_task(
        self,
        *,
        filename: str,
        file_path: str,
        tag_id: str | None = None,
        topic: str = "",
    ) -> IngestionTask:
        """Register a new ingestion task in memory and persist it to SQLite."""
        with self._lock:
            task_id = str(uuid.uuid4())
            started_at = datetime.now(timezone.utc).isoformat()
            task = IngestionTask(
                task_id=task_id,
                filename=filename,
                file_path=file_path,
                tag_id=tag_id,
                topic=topic or filename,
                status="processing",
                stage="reading",
                stage_number=1,
                stage_message="Reading document & extracting text...",
                progress_percent=10,
                thoughts=[f"Reading document: {filename}..."],
                started_at=started_at,
            )
            self._tasks[task_id] = task
            self._active_task_id = task_id

        if self._db_path:
            await asyncio.to_thread(
                self._repo.ingestion_tasks.create_task_record,
                task_id=task.task_id,
                filename=task.filename,
                file_path=task.file_path,
                tag_id=task.tag_id,
                topic=task.topic,
                stage=task.stage,
                stage_number=task.stage_number,
                stage_message=task.stage_message,
                progress_percent=task.progress_percent,
                started_at=task.started_at,
                db_path=self._db_path,
            )
        else:
            await asyncio.to_thread(
                self._repo.ingestion_tasks.create_task_record,
                task_id=task.task_id,
                filename=task.filename,
                file_path=task.file_path,
                tag_id=task.tag_id,
                topic=task.topic,
                stage=task.stage,
                stage_number=task.stage_number,
                stage_message=task.stage_message,
                progress_percent=task.progress_percent,
                started_at=task.started_at,
            )
        return task

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        stage: TaskStage | None = None,
        stage_number: int | None = None,
        stage_message: str | None = None,
        progress_percent: int | None = None,
        thoughts: list[str] | None = None,
        duplicates: list[dict] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        completed_at: str | None = None,
    ) -> IngestionTask | None:
        """Update task stage, status, thoughts, or progress in memory and SQLite."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                task = await self._hydrate_task(task_id)
                if not task:
                    return None

            if status is not None:
                task.status = status
            if stage is not None:
                task.stage = stage
            if stage_number is not None:
                task.stage_number = stage_number
            if stage_message is not None:
                task.stage_message = stage_message
            if progress_percent is not None:
                task.progress_percent = progress_percent
            if thoughts is not None:
                task.thoughts = thoughts
            if duplicates is not None:
                task.duplicates = duplicates
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error

            # Extraction has finished when review is required too; only the
            # user's duplicate decisions remain. Freeze elapsed time and
            # release the worker while keeping the review payload durable.
            if task.status in {"completed", "review_required", "failed", "cancelled"}:
                if not task.completed_at:
                    task.completed_at = completed_at or datetime.now(timezone.utc).isoformat()
                if self._active_task_id == task_id:
                    self._active_task_id = None
                self._asyncio_tasks.pop(task_id, None)

            kwargs = {
                "task_id": task.task_id,
                "status": task.status,
                "stage": task.stage,
                "stage_number": task.stage_number,
                "stage_message": task.stage_message,
                "progress_percent": task.progress_percent,
                "thoughts": task.thoughts,
                "duplicates": task.duplicates,
                "result": task.result,
                "error": task.error,
                "completed_at": task.completed_at,
            }
            if self._db_path:
                kwargs["db_path"] = self._db_path

        await asyncio.to_thread(self._repo.ingestion_tasks.update_task_record, **kwargs)
        return task

    async def append_thought(self, task_id: str, thought: str) -> None:
        """Append an LLM reasoning trace step. In-memory append is instant.
        Persists asynchronously to prevent event loop bottlenecks."""
        cleaned = (thought or "").strip()
        if not cleaned:
            return

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                task = await self._hydrate_task(task_id)
                if not task:
                    return
            task.thoughts.append(cleaned)
            kwargs = {"task_id": task_id, "thoughts": list(task.thoughts)}
            if self._db_path:
                kwargs["db_path"] = self._db_path

        await asyncio.to_thread(self._repo.ingestion_tasks.update_task_record, **kwargs)

    async def get_task(self, task_id: str) -> IngestionTask | None:
        """Fetch task by ID. Checks in-memory cache first, falls back to SQLite."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return task
        return await self._hydrate_task(task_id)

    async def get_active_or_latest_task(self) -> IngestionTask | None:
        """Retrieve the currently processing task or the most recently started task."""
        with self._lock:
            # 1. Check if designated active task is processing
            if self._active_task_id and self._active_task_id in self._tasks:
                task = self._tasks[self._active_task_id]
                if task.status == "processing":
                    return task

            # 2. Check in-memory tasks for any processing task
            for task in reversed(list(self._tasks.values())):
                if task.status == "processing":
                    self._active_task_id = task.task_id
                    return task

            # 3. Check most recent in-memory task
            if self._tasks:
                return list(self._tasks.values())[-1]

        # 4. Fallback to SQLite (e.g. fresh process or restarted sidecar)
        kwargs = {}
        if self._db_path:
            kwargs["db_path"] = self._db_path
        row = await asyncio.to_thread(self._repo.ingestion_tasks.get_active_task_record, **kwargs)
        if not row:
            row = await asyncio.to_thread(self._repo.ingestion_tasks.get_latest_task_record, **kwargs)

        if row:
            task = IngestionTask.from_record(row)
            with self._lock:
                self._tasks[task.task_id] = task
                if task.status == "processing":
                    self._active_task_id = task.task_id
            return task
        return None

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel an in-flight ingestion task."""
        with self._lock:
            async_task = self._asyncio_tasks.pop(task_id, None)
            if async_task and not (async_task.done() if hasattr(async_task, "done") else False):
                async_task.cancel()

            task = self._tasks.get(task_id)
            if not task:
                task = await self._hydrate_task(task_id)
                if not task:
                    return False

            task.status = "cancelled"
            task.stage = "failed"
            task.stage_message = "Task cancelled by user."
            task.completed_at = datetime.now(timezone.utc).isoformat()
            if self._active_task_id == task_id:
                self._active_task_id = None

            kwargs = {
                "task_id": task_id,
                "status": "cancelled",
                "stage": "failed",
                "stage_message": "Task cancelled by user.",
                "completed_at": task.completed_at,
            }
            if self._db_path:
                kwargs["db_path"] = self._db_path
        await asyncio.to_thread(self._repo.ingestion_tasks.update_task_record, **kwargs)
        return True

    def register_asyncio_task(self, task_id: str, task: Any) -> None:
        """Register the background task or future to enable cancellation."""
        self._asyncio_tasks[task_id] = task

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel workers and drain their thread executor before storage closes.

        Cancelling an ``asyncio.to_thread`` awaiter does not stop the underlying
        SQLite call.  Closing the connection pool while that call is still
        running can crash CPython's sqlite module, so shutdown explicitly waits
        for the background loop's default executor before returning.
        """
        with self._lock:
            futures = list(self._asyncio_tasks.values())
            loop = self._bg_loop
            thread = self._bg_thread

        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass

        if loop and loop.is_running():
            async def _drain() -> None:
                current = asyncio.current_task()
                pending = [task for task in asyncio.all_tasks() if task is not current]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                await loop.shutdown_default_executor()

            try:
                cleanup = asyncio.run_coroutine_threadsafe(_drain(), loop)
                cleanup.result(timeout=max(0.1, timeout))
            except Exception as exc:
                _log.debug("ingestion worker shutdown drain incomplete: %s", exc)
            finally:
                loop.call_soon_threadsafe(loop.stop)

        if thread and thread.is_alive():
            deadline = time.monotonic() + max(0.1, timeout)
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if loop and not loop.is_running() and not loop.is_closed():
            try:
                loop.close()
            except Exception as exc:
                _log.debug("ingestion worker loop close skipped: %s", exc)

        with self._lock:
            self._tasks.clear()
            self._asyncio_tasks.clear()
            self._active_task_id = None
            self._bg_loop = None
            self._bg_thread = None

    async def _hydrate_task(self, task_id: str) -> IngestionTask | None:
        """Internal helper to load a task from SQLite into cache."""
        kwargs = {"task_id": task_id}
        if self._db_path:
            kwargs["db_path"] = self._db_path
        row = await asyncio.to_thread(self._repo.ingestion_tasks.get_task_record, **kwargs)
        if not row:
            return None
        task = IngestionTask.from_record(row)
        with self._lock:
            self._tasks[task_id] = task
        return task


_global_task_manager: IngestionTaskManager | None = None


def get_ingestion_task_manager() -> IngestionTaskManager:
    global _global_task_manager
    if _global_task_manager is None:
        from data.sqlite.connection import DEFAULT_DB_PATH
        _global_task_manager = IngestionTaskManager(db_path=DEFAULT_DB_PATH)
    return _global_task_manager


def reset_ingestion_task_manager() -> None:
    """Helper for test cleanup."""
    global _global_task_manager
    if _global_task_manager is not None:
        _global_task_manager.shutdown()
    _global_task_manager = None
