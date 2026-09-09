"""User profile model (docs/03 §7, docs/04 §1).

Projects and skills carry stable ids from day one — graph-proof (docs/04) and
selected_project_ids reference them, and retrofitting ids later is painful.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Skill(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    level: str | None = None
    years: float | None = None
    tags: list[str] = Field(default_factory=list)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    role_tags: list[str] = Field(default_factory=list)
    verbatim: bool = False  # if True, never LLM-rewritten (docs/05 §3)


class Identity(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    citizenship: str | None = None
    work_authorization: str | None = None
    locations: list[str] = Field(default_factory=list)
    willing_to_relocate: bool | None = None


class Preferences(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    comp_min: float | None = None
    comp_currency: str | None = None
    remote_only: bool | None = None
    seniority_floor: str | None = None
    seniority_ceiling: str | None = None
    deal_breakers: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)  # do-not-show companies (docs/04 §5)


class Profile(BaseModel):
    user_id: UUID
    identity: Identity = Field(default_factory=Identity)
    roles: list[str] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience: list[dict] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    publications: list[dict] = Field(default_factory=list)
    certifications: list[dict] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)
    anything_else: str = ""
    profile_version: int = 1  # bumped on edit; invalidates tailor cache (docs/05 §9)
    updated_at: datetime | None = None
