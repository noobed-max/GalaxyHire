from __future__ import annotations

from generation.generators.package import _scope_profile_to_tag
from profile.provenance import (
    collect_resume_point_tags,
    reconcile_tagged_resume_documents,
)


def _profile() -> dict:
    return {
        "skills": [{"id": "sk-python", "n": "Python"}],
        "exp": [{
            "id": "exp-acme",
            "role": "Backend Engineer",
            "co": "Acme",
            "points": [
                {"id": "pt-shared", "text": "Built a reusable API platform."},
                {"id": "pt-sde", "text": "Scaled the API platform for web traffic."},
                {"id": "pt-ml", "text": "Trained ranking models for search."},
            ],
            "d": "Built a reusable API platform.\nScaled the API platform for web traffic.\nTrained ranking models for search.",
        }],
        "projects": [{
            "id": "proj-dashboard",
            "title": "ML Dashboard",
            "points": [{"id": "pt-dashboard", "text": "Shipped a model monitoring dashboard."}],
            "impact": "Shipped a model monitoring dashboard.",
        }],
    }


def test_provenance_excludes_pending_similar_and_reuses_exact_id():
    from profile.ingest_parse import point_id

    profile = _profile()
    incoming = {
        "skills": [{"n": "Python"}],
        "exp": [{
            "role": "Backend Engineer",
            "co": "Acme",
            "d": "Built a reusable API platform.\nScaled the API platform for web traffic.",
            "exact_matches": [{"existing_point_id": "pt-shared", "text": "Built a reusable API platform."}],
            "similar_pairs": [{
                "existing_point_id": "pt-sde",
                "existing_text": "Scaled the API platform for web traffic.",
                "new_text": "Improved the API platform for web traffic.",
            }],
        }],
    }

    rows = collect_resume_point_tags(incoming, "tag-sde", profile)
    assert ("experience", "exp-acme", "tag-sde") in rows
    assert ("experience", "pt-shared", "tag-sde") in rows
    assert ("experience", "pt-sde", "tag-sde") in rows
    pending_id = point_id("exp-acme", "Improved the API platform for web traffic.")
    assert ("experience", pending_id, "tag-sde") not in rows


def test_repair_two_tagged_documents_is_conservative_and_preserves_shared_points(tmp_path):
    from data.sqlite.connection import init_sql
    from data.sqlite import documents, point_tags, tags

    db = str(tmp_path / "provenance.db")
    init_sql(db)
    tag_sde = tags.create_tag("SDE", db_path=db)["id"]
    tag_ml = tags.create_tag("ML", db_path=db)["id"]
    documents.create_document(
        kind="resume", file_path="", tag_id=tag_sde,
        excerpt="Backend Engineer at Acme\nBuilt a reusable API platform.\nScaled the API platform for web traffic.",
        db_path=db,
    )
    documents.create_document(
        kind="resume", file_path="", tag_id=tag_ml,
        excerpt="Backend Engineer at Acme\nBuilt a reusable API platform.\nTrained ranking models for search.",
        db_path=db,
    )

    report = reconcile_tagged_resume_documents(profile=_profile(), db_path=db)
    assert report["status"] == "ok"
    assert report["documents_scanned"] == 2
    assert report["rows_inserted"] > 0
    rows = point_tags.list_point_tags(db_path=db)
    memberships = {(row["point_id"], row["tag_id"]) for row in rows}
    assert ("pt-shared", tag_sde) in memberships
    assert ("pt-shared", tag_ml) in memberships
    assert ("pt-sde", tag_sde) in memberships
    assert ("pt-ml", tag_ml) in memberships
    assert ("pt-sde", tag_ml) not in memberships
    assert ("pt-ml", tag_sde) not in memberships


def test_repair_does_not_match_old_bullet_inside_pending_variant():
    from profile.provenance import match_document_profile_points

    profile = {
        "exp": [{
            "id": "exp1",
            "role": "Backend Engineer",
            "co": "Acme",
            "points": [{"id": "old", "text": "Built backend."}],
        }],
        "skills": [],
        "projects": [],
    }
    document = {
        "kind": "resume",
        "tag_id": "sde",
        "excerpt": "Backend Engineer at Acme\nBuilt backend with Go.",
    }
    assert match_document_profile_points(document, profile) == []


def test_scope_rebuilds_bullets_and_retains_only_visible_entity_evidence():
    class _PointTags:
        @staticmethod
        def scoped_point_ids(tag_id):
            assert tag_id == "sde"
            return {"experience": {"pt-ml"}, "project": {"proj-dashboard", "pt-dashboard"}}

    class _Repo:
        point_tags = _PointTags()

    profile = _profile()
    scoped = _scope_profile_to_tag(profile, "sde", _Repo())
    exp = scoped["exp"][0]
    assert [point["id"] for point in exp["points"]] == ["pt-shared", "pt-sde"]
    assert "Trained ranking" not in exp["d"]
    # The project parent is tagged out and has no visible children, so it is
    # removed rather than leaking an entire unrelated entity into the build.
    assert scoped["projects"] == []
    assert profile["exp"][0]["d"].count("Trained ranking") == 1

    from generation.generators.resume import _fallback_package

    rendered = _fallback_package(scoped, {"title": "Backend Engineer"}).resume_markdown
    assert rendered.count("Built a reusable API platform") == 1
    assert rendered.count("Scaled the API platform for web traffic") == 1
    assert "Trained ranking models for search" not in rendered
