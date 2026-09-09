"""Process-local lifecycle state shared by search and danger-zone reset."""

from __future__ import annotations

from api.task_registry import TaskRegistry

SEARCH_TASK = "corpus_search"
search_tasks = TaskRegistry()
