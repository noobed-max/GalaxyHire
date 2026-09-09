"""Cooperative background-task registry.

Extracted from JustHireMe's discovery router when that router was replaced with corpus search.
Kept rather than reinvented: it is lock-guarded, supports mutual exclusion between task names,
signals cancellation through an `asyncio.Event` the task itself polls (so work stops at a safe
point instead of being torn out mid-write), and cleans up after itself. The obvious replacement —
a shared "cancelled" flag — has none of those properties.
"""

from __future__ import annotations

import asyncio

from core.logging import get_logger

_log = get_logger(__name__)


class TaskRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._stops: dict[str, asyncio.Event] = {}

    async def start(self, name: str, coro_factory, *, mutex_with: list[str] | None = None) -> bool:
        async with self._lock:
            for check_name in [name, *(mutex_with or [])]:
                task = self._tasks.get(check_name)
                if task and not task.done():
                    return False

            stop = asyncio.Event()
            self._stops[name] = stop

            async def _wrapper() -> None:
                try:
                    await coro_factory(stop)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log.error("background task %s failed: %s", name, exc)
                finally:
                    async with self._lock:
                        if self._tasks.get(name) is task:
                            self._tasks.pop(name, None)
                            self._stops.pop(name, None)

            task = asyncio.create_task(_wrapper())
            self._tasks[name] = task
            return True

    async def join(self, name: str) -> None:
        """Wait for a registered task to finish, without holding the lock while it runs.

        Exists so a caller can await a task *and* have it be cancellable. `start` returns as soon
        as the task is scheduled, so anything that needed a result had to bypass the registry
        entirely — and a task that was never registered cannot be stopped. That is precisely why
        the UI's "Stop scan" button did nothing: the synchronous /scan path awaited the search
        directly, so /scan/stop looked for a running task and found none.

        Grabbing the task under the lock and awaiting outside it matters: `_wrapper` takes the same
        lock in its `finally`, so awaiting while holding it would deadlock.
        """
        async with self._lock:
            task = self._tasks.get(name)
        if task is None:
            return
        # The task's own wrapper swallows exceptions and logs them; the caller wants completion,
        # not the failure mode, so a cancellation here is not re-raised into the request handler.
        await asyncio.gather(task, return_exceptions=True)

    async def stop(self, name: str) -> bool:
        async with self._lock:
            task = self._tasks.get(name)
            stop = self._stops.get(name)
            if not task or task.done() or stop is None:
                return False
            stop.set()
            return True

    async def is_running(self, name: str) -> bool:
        async with self._lock:
            task = self._tasks.get(name)
            return bool(task and not task.done())
