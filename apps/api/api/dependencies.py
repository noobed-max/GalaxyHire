from __future__ import annotations

from functools import lru_cache
from importlib import import_module

from data.repository import Repository, create_repository
from gateway.jobs import JobStore, get_job_store


@lru_cache
def get_repository() -> Repository:
    return create_repository()


def _local_service(module_name: str, factory_name: str):
    module = import_module(module_name)
    return getattr(module, factory_name)()


@lru_cache
def get_profile_service():
    module = import_module("profile.service")
    return module.ProfileService()


@lru_cache
def get_corpus_discovery_service():
    """Discovery is corpus retrieval now, not scraping.

    Replaces `get_discovery_service`, which built JustHireMe's own scraper stack. Jobs reach the
    corpus from `services/scraper-node`; this app only searches what is already stored (D1/D7).
    """
    return _local_service("corpus.service", "create_corpus_discovery_service")


@lru_cache
def get_ranking_service():
    return _local_service("ranking.service", "create_ranking_service")


@lru_cache
def get_generation_service():
    return _local_service("generation.service", "create_generation_service")


def get_job_runner() -> JobStore:
    return get_job_store()


def get_ingestion_task_manager():
    from api.ingestion_tasks import get_ingestion_task_manager as _get_mgr
    return _get_mgr()
