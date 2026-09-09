"""The boundary to the corpus service.

Job storage and retrieval live in `services/corpus`; this app owns the profile, the ranking
explanation, the pipeline, and the graph (ARCHITECTURE.md D1/D2). Everything that crosses that
boundary goes through here, so nothing else in the app needs to know the corpus is a separate
process.
"""

from corpus.client import CorpusClient, CorpusConfig, CorpusUnavailable, create_corpus_client
from corpus.service import (
    CorpusDiscoveryService,
    CorpusSearchResult,
    corpus_job_to_lead,
    create_corpus_discovery_service,
)

__all__ = [
    "CorpusClient",
    "CorpusConfig",
    "CorpusDiscoveryService",
    "CorpusSearchResult",
    "CorpusUnavailable",
    "corpus_job_to_lead",
    "create_corpus_client",
    "create_corpus_discovery_service",
]
