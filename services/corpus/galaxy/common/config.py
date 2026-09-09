"""Typed application configuration, loaded from environment / .env.

Nothing secret lives in source (docs/07 §5). Import `get_settings()` everywhere; it is
cached so the .env is parsed once.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- run mode -----------------------------------------------------------
    run_role: str = Field(default="api", description="api | worker | scheduler")

    # --- data layer ---------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://galaxy:galaxy@localhost:5432/galaxy"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    object_store_dir: str = Field(default="./data/objects")
    # where the Apply flow writes each job's tailored resume PDF (docs/11 §W1.4)
    resume_output_dir: str = Field(default="./data/resumes")

    # --- ingestion ----------------------------------------------------------
    ingest_hours_seed: int = Field(default=72)
    source_circuit_break_after: int = Field(
        default=5, description="consecutive failures before a source auto-disables"
    )
    proxy_pool: str = Field(default="", description="csv of user:pass@host:port; empty = direct")

    # --- ingestion ----------------------------------------------------------
    # Volume/breadth knobs for the old adapter sweep died with the GalaxyJobsAi sources;
    # collection is app-triggered (career-ops scrape) and the per-run shape is decided by the
    # scraper's config + the UI's portal selection, not by env here.

    # --- llm proxy (bring-your-own OpenAI-compatible endpoint) --------------
    llm_base_url: str = Field(default="http://127.0.0.1:8080/v1")
    llm_api_key: str = Field(default="not-needed")
    llm_model: str = Field(default="")

    # --- embeddings ---------------------------------------------------------
    embedding_version: str = Field(
        default="minilm-l6-v2", description="tag stamped on every stored vector (docs/03 §6)"
    )
    embedding_dim: int = Field(default=384)

    # --- auth ---------------------------------------------------------------
    api_keys: str = Field(default="dev-key", description="csv of accepted x-api-key values")

    @property
    def proxy_list(self) -> list[str]:
        return [p.strip() for p in self.proxy_pool.split(",") if p.strip()]

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
