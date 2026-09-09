"""Profile store (docs/03 §7).

CRUD for the user profile. Skills/projects keep stable ids (assigned by the pydantic models);
`profile_version` bumps on every write so the tailor cache can invalidate (docs/05 §9).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.models.profile import Profile

_GET = text("SELECT * FROM profiles WHERE user_id = :uid")

_UPSERT = text(
    """
    INSERT INTO profiles (
        user_id, identity, roles, skills, projects, experience, education, publications,
        certifications, preferences, anything_else, profile_version, updated_at
    ) VALUES (
        :user_id, CAST(:identity AS jsonb), CAST(:roles AS jsonb), CAST(:skills AS jsonb),
        CAST(:projects AS jsonb), CAST(:experience AS jsonb), CAST(:education AS jsonb),
        CAST(:publications AS jsonb), CAST(:certifications AS jsonb), CAST(:preferences AS jsonb),
        :anything_else, :profile_version, :updated_at
    )
    ON CONFLICT (user_id) DO UPDATE SET
        identity = EXCLUDED.identity, roles = EXCLUDED.roles, skills = EXCLUDED.skills,
        projects = EXCLUDED.projects, experience = EXCLUDED.experience,
        education = EXCLUDED.education, publications = EXCLUDED.publications,
        certifications = EXCLUDED.certifications, preferences = EXCLUDED.preferences,
        anything_else = EXCLUDED.anything_else,
        profile_version = profiles.profile_version + 1,
        updated_at = EXCLUDED.updated_at
    RETURNING profile_version
    """
)


def _row_to_profile(row) -> Profile:
    def j(v):
        return v if isinstance(v, (dict, list)) else json.loads(v) if v else None

    return Profile(
        user_id=row.user_id,
        identity=j(row.identity) or {},
        roles=j(row.roles) or [],
        skills=j(row.skills) or [],
        projects=j(row.projects) or [],
        experience=j(row.experience) or [],
        education=j(row.education) or [],
        publications=j(row.publications) or [],
        certifications=j(row.certifications) or [],
        preferences=j(row.preferences) or {},
        anything_else=row.anything_else or "",
        profile_version=row.profile_version,
        updated_at=row.updated_at,
    )


class ProfileStore:
    async def get(self, user_id: UUID) -> Profile | None:
        sm = get_sessionmaker()
        async with sm() as s:
            row = (await s.execute(_GET, {"uid": str(user_id)})).mappings().first()
            return _row_to_profile(_Row(row)) if row else None

    async def upsert(self, profile: Profile) -> Profile:
        sm = get_sessionmaker()
        async with sm() as s:
            params = {
                "user_id": str(profile.user_id),
                "identity": profile.identity.model_dump_json(),
                "roles": json.dumps(profile.roles),
                "skills": json.dumps([sk.model_dump() for sk in profile.skills]),
                "projects": json.dumps([p.model_dump() for p in profile.projects]),
                "experience": json.dumps(profile.experience),
                "education": json.dumps(profile.education),
                "publications": json.dumps(profile.publications),
                "certifications": json.dumps(profile.certifications),
                "preferences": profile.preferences.model_dump_json(),
                "anything_else": profile.anything_else,
                "profile_version": 1,
                "updated_at": datetime.now(UTC),
            }
            await s.execute(_UPSERT, params)
            await s.commit()
        refreshed = await self.get(profile.user_id)
        assert refreshed is not None
        return refreshed


class _Row:
    """Adapts a SQLAlchemy RowMapping to attribute access for _row_to_profile."""

    def __init__(self, mapping):
        self._m = mapping

    def __getattr__(self, name):
        return self._m[name]
