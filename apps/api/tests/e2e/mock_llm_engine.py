"""Deterministic Mock LLM Engine for GalaxyHire E2E Testing.

Provides offline, predictable LLM extraction responses with tri-state classification
support (exact matches, similar duplicates, new points), thought streaming, and
error simulation.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel

from models.schema import CandidateProfile, ExperienceEntry, ProjectEntry, SkillEntry


class MockLLMEngine:
    """Mock LLM Engine providing scripted and dynamic responses for E2E tests."""

    def __init__(self):
        self.recorded_calls: list[dict[str, Any]] = []
        self.scripted_profiles: dict[str, CandidateProfile] = {}
        self.default_profile: CandidateProfile | None = None
        self.should_timeout: bool = False
        self.should_fail: bool = False
        self.error_message: str = "Simulated LLM Provider Failure"
        self.simulated_thoughts: list[str] = [
            "Reading document structure & extracting plain text...",
            "Analyzing candidate roles, skills, and projects with AI...",
            "Cross-referencing existing profile context for duplicate work...",
            "Indexing canonical points and associating track tags...",
        ]

    def set_default_profile(self, profile: CandidateProfile) -> None:
        """Sets the fallback profile to return if no scripted match is found."""
        self.default_profile = profile

    def script_profile(self, key: str, profile: CandidateProfile) -> None:
        """Scripts a specific profile response when `key` is present in the prompt."""
        self.scripted_profiles[key] = profile

    def simulate_timeout(self, enable: bool = True) -> None:
        """Simulate an LLM call timeout."""
        self.should_timeout = enable

    def simulate_failure(self, enable: bool = True, message: str = "Simulated Failure") -> None:
        """Simulate an LLM API error."""
        self.should_fail = enable
        self.error_message = message

    def reset(self) -> None:
        """Reset recorded state and script rules."""
        self.recorded_calls.clear()
        self.scripted_profiles.clear()
        self.default_profile = None
        self.should_timeout = False
        self.should_fail = False

    def build_sample_sde_profile(self) -> CandidateProfile:
        """Helper returning a standard SDE candidate profile."""
        return CandidateProfile(
            n="Alex Mercer",
            s="Senior Software Engineer with 6+ years specializing in distributed systems and cloud infrastructure.",
            loc="San Francisco, CA",
            skills=[
                SkillEntry(n="Python", cat="language"),
                SkillEntry(n="Go", cat="language"),
                SkillEntry(n="FastAPI", cat="framework"),
                SkillEntry(n="Kubernetes", cat="cloud"),
                SkillEntry(n="PostgreSQL", cat="database"),
            ],
            exp=[
                ExperienceEntry(
                    role="Senior Backend Engineer",
                    co="CloudScale Inc",
                    period="Jan 2022 - Present",
                    d="Architected event-driven microservices processing 50k events/sec using Kafka.\n"
                      "Engineered zero-downtime database migration pipeline for 10TB PostgreSQL cluster.\n"
                      "Mentored 4 junior engineers on distributed systems architecture.",
                    s=["Python", "FastAPI", "Kubernetes", "Kafka"],
                ),
                ExperienceEntry(
                    role="Software Engineer",
                    co="TechCorp Solutions",
                    period="Jun 2019 - Dec 2021",
                    d="Developed RESTful APIs in Go and Python serving 2M daily active users.\n"
                      "Optimized redis caching layer reducing P99 latency by 45ms.",
                    s=["Go", "Python", "Redis"],
                ),
            ],
            projects=[
                ProjectEntry(
                    title="Distributed Task Scheduler",
                    stack=["Go", "Raft", "gRPC"],
                    repo="https://github.com/example/task-sched",
                    impact="Implemented consensus-based distributed job queue handling node failovers in <200ms.",
                    s=["Go", "Distributed Systems"],
                )
            ],
            certifications=["AWS Certified Solutions Architect Professional"],
            education=["B.S. Computer Science - UC Berkeley"],
            achievements=["1st Place Cloud Hackathon 2023"],
        )

    def build_sample_ml_profile(self) -> CandidateProfile:
        """Helper returning a standard ML candidate profile."""
        return CandidateProfile(
            n="Alex Mercer",
            s="Machine Learning Engineer specializing in LLM evaluation, fine-tuning, and vector retrieval pipelines.",
            loc="San Francisco, CA",
            skills=[
                SkillEntry(n="Python", cat="language"),
                SkillEntry(n="PyTorch", cat="framework"),
                SkillEntry(n="LanceDB", cat="database"),
                SkillEntry(n="Docker", cat="tool"),
            ],
            exp=[
                ExperienceEntry(
                    role="Machine Learning Engineer",
                    co="CloudScale Inc",
                    period="Jan 2022 - Present",
                    d="Architected event-driven microservices processing 50k events/sec using Kafka.\n"
                      "Deployed local embedding and reranking models on GPU clusters reducing inference latency by 60%.\n"
                      "Built automated LLM evaluation pipeline scoring semantic consistency across 10k synthetic test cases.",
                    s=["Python", "PyTorch", "LanceDB"],
                ),
            ],
            projects=[
                ProjectEntry(
                    title="GraphRAG Retrieval Engine",
                    stack=["Python", "Kùzu", "LanceDB"],
                    repo="https://github.com/example/graph-rag",
                    impact="Hybrid knowledge-graph and vector retrieval pipeline achieving 94% recall.",
                    s=["Kùzu", "LanceDB", "Vector Search"],
                )
            ],
            certifications=["TensorFlow Developer Certificate"],
            education=["B.S. Computer Science - UC Berkeley"],
            achievements=[],
        )

    def fake_call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        model_cls: type[BaseModel],
        step: str | None = None,
    ) -> BaseModel:
        """Mock implementation of llm.client.call_llm."""
        self.recorded_calls.append({
            "system": system_prompt,
            "user": user_prompt,
            "model_cls": model_cls,
            "step": step,
        })

        if self.should_timeout:
            import httpx
            raise httpx.TimeoutException("LLM operation timed out after 120.0s")

        if self.should_fail:
            raise RuntimeError(self.error_message)

        # Check scripted profiles by key in user_prompt
        for key, profile in self.scripted_profiles.items():
            if key.lower() in user_prompt.lower():
                if issubclass(model_cls, CandidateProfile):
                    return profile
                try:
                    return model_cls.model_validate(profile.model_dump())
                except Exception:
                    pass

        # Return default profile if provided
        if self.default_profile is not None:
            if issubclass(model_cls, CandidateProfile):
                return self.default_profile
            try:
                return model_cls.model_validate(self.default_profile.model_dump())
            except Exception:
                pass

        # If CandidateProfile is requested and nothing was scripted, generate SDE profile
        if issubclass(model_cls, CandidateProfile):
            return self.build_sample_sde_profile()

        # Fallback to default constructable instance
        try:
            return model_cls()
        except Exception:
            return model_cls.model_construct()


def install_mock_llm(monkeypatch, engine: MockLLMEngine | None = None) -> MockLLMEngine:
    """Helper to monkeypatch llm.client.call_llm with MockLLMEngine."""
    if engine is None:
        engine = MockLLMEngine()

    import llm
    import llm.client
    monkeypatch.setattr(llm.client, "call_llm", engine.fake_call_llm)
    monkeypatch.setattr(llm.client, "_call_llm_once", engine.fake_call_llm)
    monkeypatch.setattr(llm, "call_llm", engine.fake_call_llm)
    monkeypatch.setattr(llm.client, "provider_needs_key", lambda p: False)
    monkeypatch.setattr(llm, "provider_needs_key", lambda p: False)
    monkeypatch.setattr(llm.client, "resolve_config", lambda *args, **kwargs: ("mock", "mock-key", "mock-model"))
    monkeypatch.setattr(llm, "resolve_config", lambda *args, **kwargs: ("mock", "mock-key", "mock-model"))
    return engine
