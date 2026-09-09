from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class SkillEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    n: str = Field(default="", validation_alias=AliasChoices("n", "name"))
    cat: str = "general"

    @property
    def name(self) -> str:
        return self.n

    @name.setter
    def name(self, val: str) -> None:
        self.n = val


class ExactMatchBullet(BaseModel):
    existing_point_id: str = Field(
        ...,
        description="The ID of the matching existing canonical bullet point",
    )
    text: str = Field(
        default="",
        description="The canonical text of the existing bullet point",
    )


class SimilarBulletPair(BaseModel):
    existing_point_id: str = Field(
        ...,
        description="The ID of the existing bullet point being varied",
    )
    existing_text: str = Field(
        ...,
        description="The verbatim text of the existing bullet point",
    )
    new_text: str = Field(
        ...,
        description="The incoming bullet point text from the new resume",
    )
    explanation: str = Field(
        default="",
        description="Brief explanation of why these describe the same work with variations",
    )


class ExperienceEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = ""
    co: str = Field(default="", validation_alias=AliasChoices("co", "company"))
    period: str = ""
    # The model may reference an entity ID from EXISTING PROFILE CONTEXT when
    # this resume describes the same underlying job with a title variation.
    # The server validates the reference and remains the owner of canonical IDs.
    matched_entity_id: str = ""
    d: str = Field(default="", validation_alias=AliasChoices("d", "description"))
    # Semantic bullets are the source representation for extraction.  ``d``
    # remains the newline-separated legacy storage field used by the graph and
    # API, so older payloads and clients stay compatible.
    points: list[str] = Field(default_factory=list, validation_alias=AliasChoices("points", "bullets"))
    s: list[str] = Field(default_factory=list, validation_alias=AliasChoices("s", "skills"))
    exact_matches: list[ExactMatchBullet] = Field(default_factory=list)
    similar_pairs: list[SimilarBulletPair] = Field(default_factory=list)
    new_points: list[str] = Field(default_factory=list)

    @property
    def company(self) -> str:
        return self.co

    @company.setter
    def company(self, val: str) -> None:
        self.co = val

    @property
    def description(self) -> str:
        return self.d

    @description.setter
    def description(self, val: str) -> None:
        self.d = val

    @property
    def skills(self) -> list[str]:
        return self.s

    @skills.setter
    def skills(self, val: list[str]) -> None:
        self.s = val


class ProjectEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    stack: list[str] = Field(default_factory=list)
    repo: str | None = None
    matched_entity_id: str = ""
    impact: str = ""
    # See ExperienceEntry.points; this additive field is intentionally
    # backward-compatible with callers that only send ``impact``.
    points: list[str] = Field(default_factory=list, validation_alias=AliasChoices("points", "bullets"))
    s: list[str] = Field(default_factory=list, validation_alias=AliasChoices("s", "skills"))
    exact_matches: list[ExactMatchBullet] = Field(default_factory=list)
    similar_pairs: list[SimilarBulletPair] = Field(default_factory=list)
    new_points: list[str] = Field(default_factory=list)

    @property
    def skills(self) -> list[str]:
        return self.s

    @skills.setter
    def skills(self, val: list[str]) -> None:
        self.s = val


class CandidateProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Defaulted (not required) so an empty LLM fallback is still a valid model;
    # the deterministic parser / merge fills in the real name when the LLM is
    # unavailable. A required field here crashes downstream `.n` access.
    n: str = Field(default="", validation_alias=AliasChoices("n", "name"))
    s: str = Field(default="", validation_alias=AliasChoices("s", "summary"))
    # Free-text location from the CV (city/region/country), used to target
    # discovery to the candidate's region. Optional and field-agnostic.
    loc: str = ""
    # Assigned by GalaxyHire before the model call. The model echoes it for
    # provenance but never invents canonical entity/point IDs.
    resume_id: str = ""
    skills: list[SkillEntry] = Field(default_factory=list)
    exp: list[ExperienceEntry] = Field(default_factory=list, validation_alias=AliasChoices("exp", "experiences"))
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.n

    @name.setter
    def name(self, val: str) -> None:
        self.n = val

    @property
    def summary(self) -> str:
        return self.s

    @summary.setter
    def summary(self, val: str) -> None:
        self.s = val

    @property
    def experiences(self) -> list[ExperienceEntry]:
        return self.exp

    @experiences.setter
    def experiences(self, val: list[ExperienceEntry]) -> None:
        self.exp = val


ContextualExperienceExtraction = ExperienceEntry
ContextualProjectExtraction = ProjectEntry
ContextualProfileExtraction = CandidateProfile
ContextualCandidateProfile = CandidateProfile
