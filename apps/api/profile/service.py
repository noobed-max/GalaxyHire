from __future__ import annotations
import logging

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from data.graph.connection import run_graph
from data.graph import profile as graph_profile
from data.conflicts_detect import flatten_profile_units as _flatten_conflict_units
from data.conflicts_detect import sync_detection as _sync_conflict_detection
from profile.ingest_parse import points_with_ids as _profile_points_with_ids
from profile.ingest_parse import ensure_points as _profile_ensure_points
from profile.ingest_parse import canonical_experience_key as _canonical_experience_key
from profile.ingest_parse import canonical_project_key as _canonical_project_key


class ProfileService:
    def get_profile(self) -> dict:
        # ensure_points: legacy rows without point children get them derived on
        # read (functional, never writes) so the resume editor always sees
        # selectable points.
        return _profile_ensure_points(graph_profile.get_profile())

    def refresh_profile_snapshot(self) -> None:
        graph_profile.refresh_profile_snapshot()

    async def _run_post_ingest_sync(self) -> dict:
        """Rebuild Kuzu correlations and Lance vectors before ingest returns."""
        try:
            return await run_graph(graph_profile.rebuild_profile_correlations)
        except Exception as exc:
            logging.getLogger(__name__).warning('post-ingest profile graph/vector sync skipped: %s', exc)
            return {"status": "skipped", "error": str(exc)}

    def _sync_conflict_groups(self) -> dict:
        """Detect near-duplicate profile points (same work, different wording)
        and store conflict groups for the user to resolve in the UI. Runs over
        the whole profile (tags don't exist yet at ingest time); enforcement is
        per-build. Fail-open by contract: a detector failure must never break
        an otherwise-successful ingest."""
        log = logging.getLogger(__name__)
        try:
            from data.repository import create_repository
            from data.vector import embeddings

            units = _flatten_conflict_units(self.get_profile())
            # Hash-fallback embeddings have different geometry than the MiniLM
            # thresholds assume; degraded mode keeps stage 2 judge-advisory.
            status = embeddings.embedding_status()
            degraded = bool(status.get("degraded")) or status.get("mode") == "hashing"
            summary = _sync_conflict_detection(
                units,
                create_repository().conflicts,
                embed_fn=embeddings.embed_texts,
                semantic_advisory=degraded,
            )
            if summary.get("groups_created"):
                log.info(
                    "conflict detection: %d near-duplicate group(s) found (%d new)",
                    summary.get("groups_detected", 0), summary.get("groups_created", 0),
                )
            return summary
        except Exception as exc:
            log.warning('conflict-group sync skipped: %s', exc)
            return {"status": "skipped", "error": str(exc)}

    def update_candidate(self, name: str, summary: str) -> dict:
        return graph_profile.update_candidate(name, summary)

    def update_identity(self, identity: dict) -> dict:
        return graph_profile.update_identity(identity)

    def add_skill(self, name: str, category: str = "general") -> dict:
        return graph_profile.add_skill(name, category)

    def update_skill(self, skill_id: str, name: str, category: str = "general") -> dict:
        return graph_profile.update_skill(skill_id, name, category)

    def delete_skill(self, skill_id: str) -> None:
        graph_profile.delete_skill(skill_id)

    def add_experience(self, role: str, company: str, period: str, description: str) -> dict:
        return graph_profile.add_experience(role, company, period, description)

    def update_experience(self, experience_id: str, role: str, company: str, period: str, description: str) -> dict:
        return graph_profile.update_experience(experience_id, role, company, period, description)

    def delete_experience(self, experience_id: str) -> None:
        graph_profile.delete_experience(experience_id)

    def add_project(self, title: str, stack: str, repo: str, impact: str) -> dict:
        return graph_profile.add_project(title, stack, repo, impact)

    def update_project(self, project_id: str, title: str, stack: str, repo: str, impact: str) -> dict:
        return graph_profile.update_project(project_id, title, stack, repo, impact)

    def delete_project(self, project_id: str) -> None:
        graph_profile.delete_project(project_id)

    def add_education(self, title: str) -> dict:
        return graph_profile.add_education(title)

    def add_certification(self, title: str) -> dict:
        return graph_profile.add_certification(title)

    def add_achievement(self, title: str) -> dict:
        return graph_profile.add_achievement(title)

    def delete_education(self, entry: str) -> None:
        graph_profile.delete_education(entry)

    def delete_certification(self, entry: str) -> None:
        graph_profile.delete_certification(entry)

    def delete_achievement(self, entry: str) -> None:
        graph_profile.delete_achievement(entry)

    async def ingest_resume(
        self,
        raw: str = "",
        pdf_path: str | None = None,
        tag_id: str | None = None,
        resume_id: str = "",
        on_stage: Any | None = None,
        on_thought: Any | None = None,
    ):
        from profile.ingestor import ingest

        async def _emit_stage(stage: str, stage_number: int, message: str) -> None:
            if on_stage:
                try:
                    res = on_stage(stage, stage_number, message)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        async def _emit_thought(thought: str) -> None:
            if on_thought:
                try:
                    res = on_thought(thought)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        await _emit_stage("reading", 1, "Reading document & extracting text...")
        await _emit_thought(f"Reading document: {pdf_path or 'raw text'}...")

        await _emit_stage("extracting", 2, "Analyzing experiences & projects with AI...")
        await _emit_thought("Analyzing experiences & projects with AI...")

        existing_profile = await run_graph(self.get_profile)
        try:
            result = await asyncio.to_thread(
                ingest,
                raw,
                pdf_path,
                existing_profile=existing_profile,
                resume_id=resume_id,
            )
        except TypeError:
            result = await asyncio.to_thread(ingest, raw, pdf_path)
        result = _assign_existing_entity_ids(result, existing_profile)
        result = _validate_existing_point_references(result, existing_profile)
        await _emit_thought("AI extraction completed.")

        await _emit_stage("deduping", 3, "Cross-referencing existing profile for duplicates...")
        await _emit_thought("Cross-referencing existing profile for duplicates...")

        snapshot = _profile_snapshot_from_resume(
            result,
            existing_profile,
            resume_id=resume_id,
            tag_id=tag_id or "",
        )
        if graph_profile.profile_has_data(snapshot):
            await run_graph(graph_profile.forget_profile_deletions_for_profile, snapshot)
            await run_graph(graph_profile.save_profile_snapshot, snapshot)
            # Materialize only the canonical merged representation.  The old
            # flow wrote the raw extraction first, which could create a second
            # graph node for a title variant before matched_entity_id linking.
            await run_graph(graph_profile.materialize_profile_snapshot, snapshot)
            # materialize_profile_snapshot persists the supplied snapshot too,
            # but make this explicit so optional graph failures cannot replace
            # the authoritative merged profile.
            await run_graph(graph_profile.save_profile_snapshot, snapshot)
        # Duplicate semantics for uploads come from the LLM's structured
        # exact_matches/similar_pairs output.  Do not run the legacy embedding/
        # token conflict detector as a second competing authority here; the
        # manual conflict rescan remains available for legacy profile data.
        await _emit_thought("AI duplicate references validated against the canonical profile.")

        await _emit_stage("indexing", 4, "Indexing skills and tags...")
        await _emit_thought("Indexing skills and tags...")
        await self._run_post_ingest_sync()

        if tag_id:
            count = await asyncio.to_thread(self.propagate_resume_point_tags, result, tag_id, existing_profile)
            if count:
                await _emit_thought(f"Tagged {count} profile points with '{tag_id}'.")
            else:
                await _emit_thought(f"No canonical profile points were associated with '{tag_id}'.")

        await _emit_thought("Indexing complete.")
        return result

    def propagate_resume_point_tags(
        self,
        resume_profile: Any,
        tag_id: str,
        existing_profile: dict | None = None,
        db_path: str | None = None,
    ) -> int:
        """Associate only canonical points from this incoming resume with ``tag_id``.

        The helper intentionally excludes staged ``similar_pairs``.  They are
        not part of a track until the user resolves the pair; the resolution
        path is responsible for linking the chosen canonical ID.
        """
        import data.sqlite.point_tags as pt_module
        from profile.provenance import collect_resume_point_tags

        triples = collect_resume_point_tags(resume_profile, tag_id, existing_profile)
        return pt_module.add_point_tags_batch(
            triples,
            db_path=db_path,
        )

    def reconcile_tagged_resume_documents(
        self,
        *,
        document_id: str | None = None,
        tag_id: str | None = None,
        profile: dict | None = None,
        db_path: str | None = None,
    ) -> dict:
        """Repair tagged resume provenance from stored excerpts, synchronously.

        This is intentionally a thin service entry point for maintenance jobs;
        it performs no extraction or model call and returns the detailed counts
        from the deterministic provenance layer.
        """
        from profile.provenance import reconcile_tagged_resume_documents

        return reconcile_tagged_resume_documents(
            document_id=document_id,
            tag_id=tag_id,
            profile=profile,
            db_path=db_path,
        )

    backfill_tagged_resume_documents = reconcile_tagged_resume_documents
    repair_tagged_resume_documents = reconcile_tagged_resume_documents

    async def resolve_duplicate_action(
        self,
        duplicate_record: dict,
        action: str,
        tag_id: str | None = None,
        db_path: str | None = None,
    ) -> dict:
        """Atomically resolve a staged duplicate pair across graph, vector store, and point_tags.

        Actions:
        - 'use_new': Replaces bullet text in Kùzu graph/LanceDB/snapshot and migrates point_tags.
        - 'keep_both': Appends new bullet to parent in Kùzu graph/LanceDB/snapshot and links tag.
        - 'keep_existing': Discards new bullet, links incoming tag to existing point in point_tags.
        """
        import data.sqlite.point_tags as pt_module
        from data.graph import profile as graph_profile
        from profile.ingest_parse import point_id, canonical_point_key, split_points

        parent_kind = duplicate_record["parent_kind"]
        parent_id = duplicate_record["parent_id"]
        existing_point_id = duplicate_record["existing_point_id"]
        existing_text = duplicate_record["existing_text"]
        new_text = duplicate_record["new_text"]
        target_tag = tag_id or duplicate_record.get("tag_id")
        target_db = db_path or getattr(pt_module, "DEFAULT_DB_PATH", None)

        staged_entity_key = (
            _canonical_experience_key(
                str(duplicate_record.get("role") or ""),
                str(duplicate_record.get("company_name") or ""),
            )
            if parent_kind == "experience"
            else _canonical_project_key(
                str(duplicate_record.get("company_name") or duplicate_record.get("entity_title") or "")
            )
        )

        if action not in {"use_new", "keep_both", "keep_existing"}:
            raise ValueError(f"Invalid resolution action: {action}")

        profile = await run_graph(self.get_profile)

        if action == "use_new":
            actual_parent_id = parent_id
            if parent_kind == "experience":
                exp_list = profile.get("exp") or profile.get("experiences") or []
                matched = None
                for e in exp_list:
                    if str(e.get("id") or "") == parent_id:
                        matched = e
                        break
                    role = str(e.get("role") or "")
                    co = str(e.get("co") or e.get("company") or "")
                    if (
                        graph_profile.hash_id(role + co) == parent_id
                        or _canonical_experience_key(role, co) == staged_entity_key
                    ):
                        matched = e
                        break
                if matched:
                    actual_parent_id = str(matched.get("id") or parent_id)
                    old_desc = str(matched.get("d") or matched.get("description") or "")
                    if existing_text in old_desc:
                        new_desc = old_desc.replace(existing_text, new_text)
                    else:
                        bullets = split_points(old_desc)
                        ekey = canonical_point_key(existing_text)
                        replaced = False
                        new_bullets = []
                        for b in bullets:
                            if not replaced and canonical_point_key(b) == ekey:
                                new_bullets.append(new_text)
                                replaced = True
                            else:
                                new_bullets.append(b)
                        if not replaced:
                            new_bullets.append(new_text)
                        new_desc = "\n".join(new_bullets)

                    role = str(matched.get("role") or "")
                    co = str(matched.get("co") or matched.get("company") or "")
                    period = str(matched.get("period") or "")
                    await run_graph(self.update_experience, actual_parent_id, role, co, period, new_desc)
            elif parent_kind == "project":
                proj_list = profile.get("projects") or []
                matched = None
                for p in proj_list:
                    if str(p.get("id") or "") == parent_id:
                        matched = p
                        break
                    if (
                        graph_profile.hash_id(str(p.get("title") or "")) == parent_id
                        or _canonical_project_key(str(p.get("title") or ""), str(p.get("repo") or "")) == staged_entity_key
                    ):
                        matched = p
                        break
                if matched:
                    actual_parent_id = str(matched.get("id") or parent_id)
                    old_impact = str(matched.get("impact") or "")
                    if existing_text in old_impact:
                        new_impact = old_impact.replace(existing_text, new_text)
                    else:
                        bullets = split_points(old_impact)
                        ekey = canonical_point_key(existing_text)
                        replaced = False
                        new_bullets = []
                        for b in bullets:
                            if not replaced and canonical_point_key(b) == ekey:
                                new_bullets.append(new_text)
                                replaced = True
                            else:
                                new_bullets.append(b)
                        if not replaced:
                            new_bullets.append(new_text)
                        new_impact = "\n".join(new_bullets)

                    title = str(matched.get("title") or "")
                    stack_str = ", ".join(matched.get("stack", [])) if isinstance(matched.get("stack"), list) else str(matched.get("stack") or "")
                    repo = str(matched.get("repo") or "")
                    await run_graph(self.update_project, actual_parent_id, title, stack_str, repo, new_impact)

            new_pid = point_id(actual_parent_id, new_text)

            # Migrate point_tags: transfer existing tags to new_pid
            conn = pt_module.get_connection(target_db)
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO point_tags (point_kind, point_id, tag_id)
                    SELECT point_kind, ?, tag_id FROM point_tags
                    WHERE point_kind = ? AND point_id = ?
                    """,
                    (new_pid, parent_kind, existing_point_id),
                )
                conn.execute(
                    "DELETE FROM point_tags WHERE point_kind = ? AND point_id = ?",
                    (parent_kind, existing_point_id),
                )
                if target_tag:
                    conn.execute(
                        "INSERT OR IGNORE INTO point_tags (point_kind, point_id, tag_id) VALUES (?, ?, ?)",
                        (parent_kind, new_pid, target_tag),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            return {"action": "use_new", "new_point_id": new_pid}

        elif action == "keep_both":
            actual_parent_id = parent_id
            if parent_kind == "experience":
                exp_list = profile.get("exp") or profile.get("experiences") or []
                matched = None
                for e in exp_list:
                    if str(e.get("id") or "") == parent_id:
                        matched = e
                        break
                    role = str(e.get("role") or "")
                    co = str(e.get("co") or e.get("company") or "")
                    if (
                        graph_profile.hash_id(role + co) == parent_id
                        or _canonical_experience_key(role, co) == staged_entity_key
                    ):
                        matched = e
                        break
                if matched:
                    actual_parent_id = str(matched.get("id") or parent_id)
                    old_desc = str(matched.get("d") or matched.get("description") or "").strip()
                    new_desc = f"{old_desc}\n{new_text}".strip() if new_text not in old_desc else old_desc
                    role = str(matched.get("role") or "")
                    co = str(matched.get("co") or matched.get("company") or "")
                    period = str(matched.get("period") or "")
                    await run_graph(self.update_experience, actual_parent_id, role, co, period, new_desc)
            elif parent_kind == "project":
                proj_list = profile.get("projects") or []
                matched = None
                for p in proj_list:
                    if str(p.get("id") or "") == parent_id:
                        matched = p
                        break
                    if (
                        graph_profile.hash_id(str(p.get("title") or "")) == parent_id
                        or _canonical_project_key(str(p.get("title") or ""), str(p.get("repo") or "")) == staged_entity_key
                    ):
                        matched = p
                        break
                if matched:
                    actual_parent_id = str(matched.get("id") or parent_id)
                    old_impact = str(matched.get("impact") or "").strip()
                    new_impact = f"{old_impact}\n{new_text}".strip() if new_text not in old_impact else old_impact
                    title = str(matched.get("title") or "")
                    stack_str = ", ".join(matched.get("stack", [])) if isinstance(matched.get("stack"), list) else str(matched.get("stack") or "")
                    repo = str(matched.get("repo") or "")
                    await run_graph(self.update_project, actual_parent_id, title, stack_str, repo, new_impact)

            new_pid = point_id(actual_parent_id, new_text)

            if target_tag:
                pt_module.add_point_tags_batch([(parent_kind, new_pid, target_tag)], db_path=target_db)

            return {"action": "keep_both", "new_point_id": new_pid}

        elif action == "keep_existing":
            if target_tag:
                pt_module.add_point_tags_batch([(parent_kind, existing_point_id, target_tag)], db_path=target_db)
            return {"action": "keep_existing", "point_id": existing_point_id}


    async def ingest_misc(self, raw: str = "", file_path: str | None = None) -> dict:
        """Fold new misc facts into the singleton misc record (update semantics).

        Unlike ingest_resume this touches NOTHING else: no graph, no vectors, no snapshot,
        no conflict sync — misc is untyped prose for form-fill context, not profile points.
        Raises on empty input or LLM failure; the router turns that into an ingest_error and
        the previous record stays untouched.
        """
        from data.repository import create_repository
        from profile.ingest_documents import _document
        from profile.misc_merger import merge_misc

        incoming = (raw or "").strip()
        if file_path:
            incoming = (incoming + "\n" + await asyncio.to_thread(_document, file_path)).strip()
        if not incoming:
            raise ValueError("nothing to merge — paste text or upload a file first")
        repo = create_repository()
        previous = (repo.misc.get_misc() or {}).get("text", "")
        merged = await asyncio.to_thread(merge_misc, previous, incoming)
        return repo.misc.save_merged(merged)

    async def import_profile_data(self, body: Any) -> dict:
        from profile.normalization import normalize_profile_payload_report

        data = _as_dict(body)
        data, import_report = normalize_profile_payload_report(data)
        errors: list[str] = []
        stats = {key: 0 for key in ["skills", "experience", "projects", "education", "certifications", "achievements"]}
        existing_snapshot = await run_graph(self.get_profile)
        imported_snapshot = _profile_snapshot_from_import(data, existing_snapshot)
        if graph_profile.profile_has_data(imported_snapshot):
            await run_graph(graph_profile.forget_profile_deletions_for_profile, imported_snapshot)
            await run_graph(graph_profile.save_profile_snapshot, imported_snapshot)

        with graph_profile.bulk_profile_import():
            candidate = _as_dict(data.get("candidate") or {})
            candidate_name = candidate.get("name", candidate.get("n", ""))
            candidate_summary = candidate.get("summary", candidate.get("s", ""))
            if candidate_name or candidate_summary:
                try:
                    await run_graph(self.update_candidate, candidate_name, candidate_summary)
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"candidate: {exc}")

            identity = _as_dict(data.get("identity") or {})
            identity_map = {
                "email": identity.get("email", ""),
                "phone": identity.get("phone", ""),
                "linkedin_url": identity.get("linkedin_url", ""),
                "github_url": identity.get("github_url", ""),
                "website_url": identity.get("website_url", ""),
                "city": identity.get("city", ""),
            }
            if any(identity_map.values()):
                try:
                    await run_graph(self.update_identity, {key: value for key, value in identity_map.items() if value})
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"identity: {exc}")

            for skill in data.get("skills", []) or []:
                item = _as_dict(skill)
                try:
                    await run_graph(self.add_skill, item.get("name", item.get("n", "")), item.get("category", item.get("cat", "general")))
                    stats["skills"] += 1
                except Exception as log_exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', log_exc)
                    pass

            for exp in data.get("experience", []) or []:
                item = _as_dict(exp)
                role = item.get("role", "")
                try:
                    await run_graph(
                        self.add_experience,
                        role,
                        item.get("company", item.get("co", "")),
                        item.get("period", ""),
                        item.get("description", item.get("d", "")),
                    )
                    stats["experience"] += 1
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"exp {role}: {exc}")

            for project in data.get("projects", []) or []:
                item = _as_dict(project)
                title = item.get("title", "")
                try:
                    await run_graph(self.add_project, title, item.get("stack", ""), item.get("repo", ""), item.get("impact", ""))
                    stats["projects"] += 1
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"proj {title}: {exc}")

            for edu in data.get("education", []) or []:
                title = _entry_title(edu)
                try:
                    await run_graph(self.add_education, title)
                    stats["education"] += 1
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"edu: {exc}")

            for cert in data.get("certifications", []) or []:
                title = _entry_title(cert)
                try:
                    await run_graph(self.add_certification, title)
                    stats["certifications"] += 1
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"cert: {exc}")

            for achievement in data.get("achievements", []) or []:
                title = _entry_title(achievement)
                try:
                    await run_graph(self.add_achievement, title)
                    stats["achievements"] += 1
                except Exception as exc:
                    logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                    errors.append(f"achievement: {exc}")

        try:
            await run_graph(self.refresh_profile_snapshot)
        except Exception as exc:
            logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
            errors.append(f"profile refresh: {exc}")

        if graph_profile.profile_has_data(imported_snapshot):
            try:
                await run_graph(graph_profile.save_profile_snapshot, imported_snapshot)
            except Exception as exc:
                logging.getLogger(__name__).warning('suppressed exception in import_profile_data: %s', exc)
                errors.append(f"profile snapshot fallback: {exc}")

        sync_status = await self._run_post_ingest_sync()
        vector_status = sync_status.get("vectors", sync_status)

        # `imported` reflects what actually reached the graph (stats), not just what
        # normalized — keep the two consistent for the summary.
        import_report["imported"] = {key: stats.get(key, 0) for key in import_report.get("imported", {})}
        summary = _summarize_import(stats, import_report)
        return {
            "status": "ok" if not errors else "partial",
            "stats": {**stats, "vector_sync": vector_status, "graph_sync": sync_status},
            "errors": errors,
            # Additive transparency (existing keys above are unchanged): a one-line
            # human summary + a structured report of received/imported/skipped/capped.
            "summary": summary,
            "report": import_report,
        }


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _assign_existing_entity_ids(profile: Any, existing: dict | None) -> Any:
    """Validate/model-assist entity linking without letting the LLM own IDs."""
    if not existing or not hasattr(profile, "exp"):
        return profile

    def tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

    def years(value: str) -> set[str]:
        return set(re.findall(r"(?:19|20)\d{2}", str(value or "")))

    exp_rows = [row for row in existing.get("exp", []) if isinstance(row, dict) and row.get("id")]
    exp_by_id = {str(row["id"]): row for row in exp_rows}

    def compatible_experience(entry, row: dict) -> bool:
        entry_company = _canonical_experience_key("", entry.co).rsplit(":", 1)[-1]
        row_company = _canonical_experience_key(
            "", str(row.get("co") or row.get("company") or "")
        ).rsplit(":", 1)[-1]
        if not entry_company or entry_company != row_company:
            return False
        entry_years = years(entry.period)
        row_years = years(str(row.get("period") or ""))
        if entry_years and row_years and entry_years.isdisjoint(row_years):
            return False
        entry_roles = tokens(entry.role)
        row_roles = tokens(str(row.get("role") or row.get("position") or ""))
        shared = entry_roles & row_roles
        if not shared:
            return False
        specific = shared - {"intern", "engineer", "developer", "lead", "senior", "junior"}
        return bool(specific or (entry_years and row_years and entry_years == row_years))

    for entry in profile.exp:
        requested = str(getattr(entry, "matched_entity_id", "") or "")
        if requested in exp_by_id and compatible_experience(entry, exp_by_id[requested]):
            continue
        entry.matched_entity_id = ""
        candidates: list[dict] = []
        for row in exp_rows:
            if compatible_experience(entry, row):
                candidates.append(row)
        if len(candidates) == 1:
            entry.matched_entity_id = str(candidates[0]["id"])

    project_rows = [row for row in existing.get("projects", []) if isinstance(row, dict) and row.get("id")]
    project_by_id = {str(row["id"]): row for row in project_rows}
    project_by_key = {
        _canonical_project_key(str(row.get("title") or ""), str(row.get("repo") or "")): row
        for row in project_rows
    }
    for entry in profile.projects:
        requested = str(getattr(entry, "matched_entity_id", "") or "")
        entry_key = _canonical_project_key(entry.title, entry.repo or "")
        if requested in project_by_id and _canonical_project_key(
            str(project_by_id[requested].get("title") or ""),
            str(project_by_id[requested].get("repo") or ""),
        ) == entry_key:
            continue
        entry.matched_entity_id = ""
        match = project_by_key.get(entry_key)
        if match:
            entry.matched_entity_id = str(match["id"])
    return profile


def _validate_existing_point_references(profile: Any, existing: dict | None) -> Any:
    """Validate LLM duplicate references without reclassifying semantics.

    The model decides whether two bullets are exact/similar/new, but it may
    only reference point IDs that belong to the already-validated canonical
    entity.  Text is rebound to the stored canonical bullet so a hallucinated
    ID or altered ``existing_text`` can never escape into tag propagation or
    duplicate review.
    """
    from profile.ingest_parse import canonical_point_key, points_with_ids

    existing = existing if isinstance(existing, dict) else {}

    def rows(kind: str) -> dict[str, dict]:
        source = existing.get("exp" if kind == "experience" else "projects") or []
        return {
            str(row.get("id")): row
            for row in source
            if isinstance(row, dict) and row.get("id")
        }

    def validate(entry: Any, row: dict | None, blob_key: str) -> None:
        incoming_by_key = {
            canonical_point_key(text): text
            for text in list(getattr(entry, "points", None) or [])
            if canonical_point_key(text)
        }
        if not row:
            entry.exact_matches = []
            entry.similar_pairs = []
            entry.new_points = [
                incoming_by_key[key]
                for key in incoming_by_key
            ]
            return

        raw_points = row.get("points") or []
        if not raw_points:
            raw_points = points_with_ids(
                str(row.get("id") or "entity"),
                str(row.get(blob_key) or row.get("description") or ""),
            )
        by_id = {
            str(point.get("id")): point
            for point in raw_points
            if isinstance(point, dict) and point.get("id") and point.get("text")
        }

        exact = []
        seen_exact: set[tuple[str, str]] = set()
        for match in list(getattr(entry, "exact_matches", None) or []):
            point = by_id.get(str(match.existing_point_id or ""))
            if not point:
                continue
            key = canonical_point_key(str(point.get("text") or ""))
            if not key or key not in incoming_by_key:
                continue
            marker = (str(match.existing_point_id), key)
            if marker in seen_exact:
                continue
            seen_exact.add(marker)
            match.text = str(point["text"])
            exact.append(match)
        entry.exact_matches = exact

        similar = []
        seen_similar: set[tuple[str, str]] = set()
        for pair in list(getattr(entry, "similar_pairs", None) or []):
            point = by_id.get(str(pair.existing_point_id or ""))
            new_key = canonical_point_key(str(pair.new_text or ""))
            if not point or not new_key or new_key not in incoming_by_key:
                continue
            marker = (str(pair.existing_point_id), new_key)
            if marker in seen_similar:
                continue
            seen_similar.add(marker)
            pair.existing_text = str(point["text"])
            pair.new_text = incoming_by_key[new_key]
            similar.append(pair)
        entry.similar_pairs = similar

        entry.new_points = [
            incoming_by_key[key]
            for text in list(getattr(entry, "new_points", None) or [])
            for key in [canonical_point_key(text)]
            if key in incoming_by_key
        ]

    exp_by_id = rows("experience")
    for entry in getattr(profile, "exp", []) or []:
        validate(entry, exp_by_id.get(str(entry.matched_entity_id or "")), "d")

    project_by_id = rows("project")
    for entry in getattr(profile, "projects", []) or []:
        validate(entry, project_by_id.get(str(entry.matched_entity_id or "")), "impact")

    return profile


_IMPORT_LABELS = (
    ("skills", "skill", "skills"),
    ("experience", "role", "roles"),
    ("projects", "project", "projects"),
    ("education", "education entry", "education entries"),
    ("certifications", "certification", "certifications"),
    ("achievements", "achievement", "achievements"),
)


def _summarize_import(stats: dict, report: dict) -> str:
    """One-line human summary of an import: what landed, what was skipped, what was
    capped. Clauses with a zero count are omitted."""
    imported = [
        f"{stats[key]} {singular if stats[key] == 1 else plural}"
        for key, singular, plural in _IMPORT_LABELS
        if stats.get(key)
    ]
    head = "Imported " + ", ".join(imported) if imported else "Nothing was imported"
    tails: list[str] = []
    skipped_total = sum(int(item.get("count", 0)) for item in report.get("skipped", []))
    if skipped_total:
        tails.append(f"skipped {skipped_total}")
    for capped in report.get("capped", []):
        tails.append(f"capped {capped['field']} {capped['original']}->{capped['kept']}")
    return head + ("" if not tails else "; " + ", ".join(tails))


def _entry_title(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(_as_dict(value).get("title", ""))


def _profile_snapshot_from_import(
    data: dict,
    existing: dict | None = None,
    *,
    resume_id: str = "",
    tag_id: str = "",
) -> dict:
    existing = graph_profile.normal_profile(existing)
    incoming = graph_profile.empty_profile()

    candidate = _as_dict(data.get("candidate") or {})
    candidate_name = str(candidate.get("name", candidate.get("n", existing.get("n", ""))) or "")
    candidate_summary = str(candidate.get("summary", candidate.get("s", existing.get("s", ""))) or "")
    incoming["n"] = candidate_name.strip()
    incoming["s"] = candidate_summary.strip()

    incoming["skills"] = [
        {
            "id": graph_profile.hash_id(str(item.get("name", item.get("n", "")) or "").strip()),
            "n": str(item.get("name", item.get("n", "")) or "").strip(),
            "cat": str(item.get("category", item.get("cat", "general")) or "general").strip() or "general",
            "source_resume_ids": [resume_id] if resume_id else list(item.get("source_resume_ids") or []),
            "tag_ids": [tag_id] if tag_id else list(item.get("tag_ids") or []),
        }
        for raw in data.get("skills", []) or []
        for item in [_as_dict(raw)]
        if str(item.get("name", item.get("n", "")) or "").strip()
    ]

    existing_exp_ids = {str(row.get("id")) for row in existing.get("exp", []) if isinstance(row, dict) and row.get("id")}
    incoming["exp"] = [
        {
            "id": (
                str(item.get("matched_entity_id"))
                if str(item.get("matched_entity_id") or "") in existing_exp_ids
                else graph_profile.hash_id(str(item.get("role", "")) + str(item.get("company", item.get("co", ""))))
            ),
            "role": str(item.get("role", "") or "").strip(),
            "co": str(item.get("company", item.get("co", "")) or "").strip(),
            "period": str(item.get("period", "") or "").strip(),
            "d": str(item.get("description", item.get("d", "")) or "").strip(),
            # When the LLM supplied explicit semantic boundaries, keep that
            # array authoritative.  The legacy description is only a fallback.
            "points": list(item.get("points") or []),
            "matched_entity_id": str(item.get("matched_entity_id") or ""),
            "exact_matches": list(item.get("exact_matches") or []),
            "similar_pairs": list(item.get("similar_pairs") or []),
            "new_points": list(item.get("new_points") or []),
            "source_resume_ids": [resume_id] if resume_id else list(item.get("source_resume_ids") or []),
            "tag_ids": [tag_id] if tag_id else list(item.get("tag_ids") or []),
            "role_variants": [{
                "title": str(item.get("role", "") or "").strip(),
                "company": str(item.get("company", item.get("co", "")) or "").strip(),
                "period": str(item.get("period", "") or "").strip(),
                "resume_id": resume_id,
                "tag_id": tag_id,
            }] if str(item.get("role", "") or "").strip() else [],
        }
        for raw in data.get("experience", []) or []
        for item in [_as_dict(raw)]
        if str(item.get("role", "") or item.get("company", item.get("co", "")) or "").strip()
    ]
    for entry in incoming["exp"]:
        # Point-level extraction: each description bullet becomes an individually
        # selectable point ({id, text}) belonging to its parent entry.
        explicit = entry.get("points") or []
        if explicit:
            from profile.ingest_parse import merge_canonical_points

            # Build only the incoming résumé's points here.  Existing points
            # are merged later by _merge_experiences; including them now would
            # incorrectly grant the new résumé/tag provenance to old bullets.
            entry["points"], _ = merge_canonical_points(entry["id"], [], explicit)
        else:
            entry["points"] = _profile_points_with_ids(entry["id"], entry["d"])
        entry["d"] = "\n".join(point["text"] for point in entry["points"])
        for point in entry["points"]:
            if resume_id:
                point["source_resume_ids"] = list(dict.fromkeys([*(point.get("source_resume_ids") or []), resume_id]))
            if tag_id:
                point["tag_ids"] = list(dict.fromkeys([*(point.get("tag_ids") or []), tag_id]))

    existing_project_ids = {str(row.get("id")) for row in existing.get("projects", []) if isinstance(row, dict) and row.get("id")}
    incoming["projects"] = [
        {
            "id": (
                str(item.get("matched_entity_id"))
                if str(item.get("matched_entity_id") or "") in existing_project_ids
                else graph_profile.hash_id(str(item.get("title", "") or "").strip())
            ),
            "title": str(item.get("title", "") or "").strip(),
            "stack": graph_profile.stack_list(item.get("stack", "")),
            "repo": str(item.get("repo", "") or "").strip(),
            "impact": str(item.get("impact", "") or "").strip(),
            "points": list(item.get("points") or []),
            "matched_entity_id": str(item.get("matched_entity_id") or ""),
            "exact_matches": list(item.get("exact_matches") or []),
            "similar_pairs": list(item.get("similar_pairs") or []),
            "new_points": list(item.get("new_points") or []),
            "source_resume_ids": [resume_id] if resume_id else list(item.get("source_resume_ids") or []),
            "tag_ids": [tag_id] if tag_id else list(item.get("tag_ids") or []),
            "title_variants": [{
                "title": str(item.get("title", "") or "").strip(),
                "resume_id": resume_id,
                "tag_id": tag_id,
            }] if str(item.get("title", "") or "").strip() else [],
        }
        for raw in data.get("projects", []) or []
        for item in [_as_dict(raw)]
        if str(item.get("title", "") or "").strip()
    ]
    for entry in incoming["projects"]:
        explicit = entry.get("points") or []
        if explicit:
            from profile.ingest_parse import merge_canonical_points

            entry["points"], _ = merge_canonical_points(entry["id"], [], explicit)
        else:
            entry["points"] = _profile_points_with_ids(entry["id"], entry["impact"])
        entry["impact"] = "\n".join(point["text"] for point in entry["points"])
        for point in entry["points"]:
            if resume_id:
                point["source_resume_ids"] = list(dict.fromkeys([*(point.get("source_resume_ids") or []), resume_id]))
            if tag_id:
                point["tag_ids"] = list(dict.fromkeys([*(point.get("tag_ids") or []), tag_id]))

    incoming["education"] = [_entry_title(item).strip() for item in data.get("education", []) or [] if _entry_title(item).strip()]
    incoming["certifications"] = [_entry_title(item).strip() for item in data.get("certifications", []) or [] if _entry_title(item).strip()]
    incoming["achievements"] = [_entry_title(item).strip() for item in data.get("achievements", []) or [] if _entry_title(item).strip()]
    incoming_identity = _as_dict(data.get("identity") or {})
    incoming["identity"] = {
        key: str(incoming_identity.get(key) or existing.get("identity", {}).get(key, "") or "").strip()
        for key in graph_profile.IDENTITY_KEYS
    }

    return _merge_profile_snapshots(existing, incoming)


def _profile_snapshot_from_resume(
    profile: Any,
    existing: dict | None = None,
    *,
    resume_id: str = "",
    tag_id: str = "",
) -> dict:
    data = profile.model_dump() if hasattr(profile, "model_dump") else _as_dict(profile)
    incoming = {
        "candidate": {
            "name": data.get("n", ""),
            "summary": data.get("s", ""),
        },
        "skills": [
            {"name": item.get("n", ""), "category": item.get("cat", "general")}
            for item in data.get("skills", []) or []
            if isinstance(item, dict) and item.get("n")
        ],
        "experience": [
            {
                "role": item.get("role", ""),
                "company": item.get("co", ""),
                "period": item.get("period", ""),
                "description": item.get("d", ""),
                "points": list(item.get("points") or []),
                "matched_entity_id": item.get("matched_entity_id", ""),
                "exact_matches": item.get("exact_matches", []),
                "similar_pairs": item.get("similar_pairs", []),
                "new_points": item.get("new_points", []),
            }
            for item in data.get("exp", []) or []
            if isinstance(item, dict)
        ],
        "projects": [
            {
                "title": item.get("title", ""),
                "stack": ", ".join(item.get("stack", []) or []) if isinstance(item.get("stack"), list) else item.get("stack", ""),
                "repo": item.get("repo", "") or "",
                "impact": item.get("impact", ""),
                "points": list(item.get("points") or []),
                "matched_entity_id": item.get("matched_entity_id", ""),
                "exact_matches": item.get("exact_matches", []),
                "similar_pairs": item.get("similar_pairs", []),
                "new_points": item.get("new_points", []),
            }
            for item in data.get("projects", []) or []
            if isinstance(item, dict)
        ],
        "education": data.get("education", []) or [],
        "certifications": data.get("certifications", []) or [],
        "achievements": data.get("achievements", []) or [],
    }
    return _profile_snapshot_from_import(
        incoming,
        existing,
        resume_id=resume_id or str(data.get("resume_id") or ""),
        tag_id=tag_id,
    )


def _merge_entity_metadata(target: dict, incoming: dict, variant_key: str) -> None:
    for field in ("source_resume_ids", "tag_ids"):
        target[field] = list(dict.fromkeys([
            *(str(value) for value in target.get(field, []) if str(value)),
            *(str(value) for value in incoming.get(field, []) if str(value)),
        ]))
    variants: list[dict] = []
    seen: set[tuple] = set()
    for raw in [*(target.get(variant_key) or []), *(incoming.get(variant_key) or [])]:
        if not isinstance(raw, dict):
            continue
        key = tuple(str(raw.get(field) or "").strip().casefold() for field in ("title", "company", "period", "resume_id", "tag_id"))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        variants.append(dict(raw))
    target[variant_key] = variants


def _merge_experiences(existing_exp: list[dict], incoming_exp: list[dict]) -> tuple[list[dict], list[str]]:
    from profile.ingest_parse import merge_canonical_points

    merged: list[dict] = []
    existing_map: dict[str, dict] = {}
    existing_by_id: dict[str, dict] = {}

    for item in existing_exp:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("position") or "").strip()
        co = str(item.get("co") or item.get("company") or "").strip()
        if role and not item.get("role_variants"):
            item["role_variants"] = [{
                "title": role,
                "company": co,
                "period": str(item.get("period") or ""),
                "resume_id": "",
                "tag_id": "",
            }]
        key = _canonical_experience_key(role, co)
        if not key:
            continue
        if key in existing_map:
            # Repair duplicate entity rows already present in older snapshots
            # without discarding their distinct bullet content.  The first row
            # keeps its ID so tags/selections continue to resolve.
            target = existing_map[key]
            target_id = str(target.get("id") or graph_profile.hash_id(f"{role}{co}"))
            target["id"] = target_id
            source_points = item.get("points") or item.get("d") or item.get("description") or ""
            merged_pts, _ = merge_canonical_points(target_id, target.get("points"), source_points)
            target["points"] = merged_pts
            target["d"] = "\n".join(p["text"] for p in merged_pts)
            if "description" in target:
                target["description"] = target["d"]
            if not target.get("period") and item.get("period"):
                target["period"] = item["period"]
            target_skills = target.get("skills") or target.get("s") or []
            item_skills = item.get("skills") or item.get("s") or []
            if item_skills:
                target["skills"] = _dedupe_text_items([*target_skills, *item_skills])
            _merge_entity_metadata(target, item, "role_variants")
            if item.get("id"):
                existing_by_id[str(item["id"])] = target
            continue
        item_id = str(item.get("id") or graph_profile.hash_id(f"{role}{co}"))
        item["id"] = item_id
        pts = item.get("points")
        if not pts and (item.get("d") or item.get("description")):
            pts, _ = merge_canonical_points(item_id, [], item.get("d") or item.get("description"))
            item["points"] = pts
            item["d"] = "\n".join(p["text"] for p in pts)
        existing_map[key] = item
        existing_by_id[item_id] = item
        merged.append(item)

    all_canonical_ids: list[str] = []

    for inc in incoming_exp:
        if not isinstance(inc, dict):
            continue
        role = str(inc.get("role") or inc.get("position") or "").strip()
        co = str(inc.get("co") or inc.get("company") or "").strip()
        key = _canonical_experience_key(role, co)

        matched_id = str(inc.get("matched_entity_id") or inc.get("id") or "")
        target = existing_by_id.get(matched_id) or existing_map.get(key)
        if target is not None:
            incoming_pts = inc.get("points") or inc.get("d") or inc.get("description") or ""
            sim_pairs = inc.get("similar_pairs") or []
            if sim_pairs:
                from profile.ingest_parse import canonical_point_key, split_points
                staged_keys = {
                    canonical_point_key(sp.get("new_text") if isinstance(sp, dict) else getattr(sp, "new_text", ""))
                    for sp in sim_pairs
                }
                if isinstance(incoming_pts, str):
                    incoming_pts = [p for p in split_points(incoming_pts) if canonical_point_key(p) not in staged_keys]
                elif isinstance(incoming_pts, list):
                    incoming_pts = [
                        p for p in incoming_pts
                        if canonical_point_key(p.get("text", "") if isinstance(p, dict) else str(p)) not in staged_keys
                    ]
            target_id = str(target.get("id") or inc.get("id") or graph_profile.hash_id(f"{role}{co}"))
            target["id"] = target_id
            merged_pts, assigned_ids = merge_canonical_points(target_id, target.get("points"), incoming_pts)
            target["points"] = merged_pts
            target["d"] = "\n".join(p["text"] for p in merged_pts)
            if "description" in target:
                target["description"] = target["d"]
            all_canonical_ids.extend(assigned_ids)

            if not target.get("period") and inc.get("period"):
                target["period"] = inc["period"]
            inc_skills = inc.get("skills") or inc.get("s") or []
            if inc_skills:
                target_skills = target.get("skills") or target.get("s") or []
                target["skills"] = _dedupe_text_items([*target_skills, *inc_skills])
            _merge_entity_metadata(target, inc, "role_variants")
        else:
            inc_id = str(inc.get("id") or graph_profile.hash_id(f"{role}{co}"))
            inc["id"] = inc_id
            inc_pts = inc.get("points") or []
            if not inc_pts and (inc.get("d") or inc.get("description")):
                inc_pts, assigned_ids = merge_canonical_points(inc_id, [], inc.get("d") or inc.get("description"))
                inc["points"] = inc_pts
                inc["d"] = "\n".join(p["text"] for p in inc_pts)
                all_canonical_ids.extend(assigned_ids)
            else:
                for p in inc_pts:
                    if isinstance(p, dict) and p.get("id"):
                        all_canonical_ids.append(p["id"])
            existing_map[key] = inc
            existing_by_id[inc_id] = inc
            merged.append(inc)

    return merged, all_canonical_ids


def _merge_projects(existing_projects: list[dict], incoming_projects: list[dict]) -> tuple[list[dict], list[str]]:
    from profile.ingest_parse import merge_canonical_points

    merged: list[dict] = []
    existing_map: dict[str, dict] = {}
    existing_by_id: dict[str, dict] = {}

    for item in existing_projects:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        repo = str(item.get("repo") or "").strip()
        if title and not item.get("title_variants"):
            item["title_variants"] = [{"title": title, "resume_id": "", "tag_id": ""}]
        key = _canonical_project_key(title, repo)
        if not key:
            continue
        if key in existing_map:
            target = existing_map[key]
            target_id = str(target.get("id") or graph_profile.hash_id(title))
            target["id"] = target_id
            source_points = item.get("points") or item.get("impact") or ""
            merged_pts, _ = merge_canonical_points(target_id, target.get("points"), source_points)
            target["points"] = merged_pts
            target["impact"] = "\n".join(p["text"] for p in merged_pts)
            if not target.get("repo") and repo:
                target["repo"] = repo
            target_stack = graph_profile.stack_list(target.get("stack"))
            item_stack = graph_profile.stack_list(item.get("stack"))
            if item_stack:
                target["stack"] = graph_profile.stack_list(_dedupe_text_items([*target_stack, *item_stack]))
            _merge_entity_metadata(target, item, "title_variants")
            if item.get("id"):
                existing_by_id[str(item["id"])] = target
            continue
        item_id = str(item.get("id") or graph_profile.hash_id(title))
        item["id"] = item_id
        pts = item.get("points")
        if not pts and item.get("impact"):
            pts, _ = merge_canonical_points(item_id, [], item.get("impact"))
            item["points"] = pts
            item["impact"] = "\n".join(p["text"] for p in pts)
        existing_map[key] = item
        existing_by_id[item_id] = item
        merged.append(item)

    all_canonical_ids: list[str] = []

    for inc in incoming_projects:
        if not isinstance(inc, dict):
            continue
        title = str(inc.get("title") or "").strip()
        repo = str(inc.get("repo") or "").strip()
        key = _canonical_project_key(title, repo)

        matched_id = str(inc.get("matched_entity_id") or inc.get("id") or "")
        target = existing_by_id.get(matched_id) or existing_map.get(key)
        if target is not None:
            incoming_pts = inc.get("points") or inc.get("impact") or ""
            sim_pairs = inc.get("similar_pairs") or []
            if sim_pairs:
                from profile.ingest_parse import canonical_point_key, split_points
                staged_keys = {
                    canonical_point_key(sp.get("new_text") if isinstance(sp, dict) else getattr(sp, "new_text", ""))
                    for sp in sim_pairs
                }
                if isinstance(incoming_pts, str):
                    incoming_pts = [p for p in split_points(incoming_pts) if canonical_point_key(p) not in staged_keys]
                elif isinstance(incoming_pts, list):
                    incoming_pts = [
                        p for p in incoming_pts
                        if canonical_point_key(p.get("text", "") if isinstance(p, dict) else str(p)) not in staged_keys
                    ]
            target_id = str(target.get("id") or inc.get("id") or graph_profile.hash_id(title))
            target["id"] = target_id
            merged_pts, assigned_ids = merge_canonical_points(target_id, target.get("points"), incoming_pts)
            target["points"] = merged_pts
            target["impact"] = "\n".join(p["text"] for p in merged_pts)
            all_canonical_ids.extend(assigned_ids)

            if not target.get("repo") and inc.get("repo"):
                target["repo"] = inc["repo"]
            inc_stack = graph_profile.stack_list(inc.get("stack"))
            target_stack = graph_profile.stack_list(target.get("stack"))
            target["stack"] = graph_profile.stack_list(_dedupe_text_items([*target_stack, *inc_stack]))
            _merge_entity_metadata(target, inc, "title_variants")
        else:
            inc_id = str(inc.get("id") or graph_profile.hash_id(title))
            inc["id"] = inc_id
            inc_pts = inc.get("points") or []
            if not inc_pts and inc.get("impact"):
                inc_pts, assigned_ids = merge_canonical_points(inc_id, [], inc.get("impact"))
                inc["points"] = inc_pts
                inc["impact"] = "\n".join(p["text"] for p in inc_pts)
                all_canonical_ids.extend(assigned_ids)
            else:
                for p in inc_pts:
                    if isinstance(p, dict) and p.get("id"):
                        all_canonical_ids.append(p["id"])
            existing_map[key] = inc
            existing_by_id[inc_id] = inc
            merged.append(inc)

    return merged, all_canonical_ids


def _merge_profile_snapshots(existing: dict, incoming: dict) -> dict:
    merged = graph_profile.normal_profile(existing)
    incoming = graph_profile.normal_profile(incoming)
    if incoming.get("n") or incoming.get("s"):
        merged["n"] = incoming.get("n") or merged.get("n", "")
        merged["s"] = incoming.get("s") or merged.get("s", "")
    merged["identity"] = {**(merged.get("identity") or {}), **{k: v for k, v in (incoming.get("identity") or {}).items() if v}}

    # Non-destructive experiences merge
    merged_exp, _exp_point_ids = _merge_experiences(merged.get("exp") or [], incoming.get("exp") or [])
    merged["exp"] = merged_exp

    # Non-destructive projects merge
    merged_proj, _proj_point_ids = _merge_projects(merged.get("projects") or [], incoming.get("projects") or [])
    merged["projects"] = merged_proj

    # Skills deduplication
    merged["skills"] = _dedupe_dict_items([*(merged.get("skills") or []), *(incoming.get("skills") or [])], "id")

    for key in ["education", "certifications", "achievements"]:
        merged[key] = _dedupe_text_items([*(merged.get(key) or []), *(incoming.get(key) or [])])

    return merged


def _norm_key(value: Any) -> str:
    # Lossy key for free-text items (education/certifications/achievements) where
    # punctuation/spacing variants of the SAME entry should de-duplicate (an entry
    # that differs only in dashes/commas/spacing collapses to one).
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _norm_key_strict(value: Any) -> str:
    # Punctuation-preserving key for skill/project/experience dedup, where tech-name
    # punctuation is significant: collapsing it dropped genuinely-distinct skills
    # (C vs C++ vs C#, .NET vs NET). Matches the [a-z0-9+#.-] class the rest of the
    # codebase treats as significant for skills.
    return re.sub(r"[^a-z0-9+#.-]+", "", str(value or "").lower())


def _dedupe_dict_items(items: list[dict], id_key: str) -> list[dict]:
    seen: set[str] = set()
    by_key: dict[str, dict] = {}
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Content-based key so the same job/project extracted twice with minor
        # text differences (which produce different id hashes) still de-dupes.
        role = str(item.get("role") or "").strip()
        company = str(item.get("co") or item.get("company") or "").strip()
        if role or company:
            key = _canonical_experience_key(role, company)
        else:
            # Skills/projects: keep C vs C++ vs C# distinct so a real skill isn't
            # dropped as a false duplicate.
            key = _canonical_project_key(
                str(item.get("title") or item.get("n") or item.get(id_key) or ""),
                str(item.get("repo") or ""),
            )
        if not key:
            continue
        if key in seen:
            target = by_key[key]
            for field in ("source_resume_ids", "tag_ids"):
                target[field] = list(dict.fromkeys([
                    *(str(value) for value in target.get(field, []) if str(value)),
                    *(str(value) for value in item.get(field, []) if str(value)),
                ]))
            continue
        seen.add(key)
        by_key[key] = item
        out.append(item)
    return out


def _dedupe_text_items(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = _entry_title(item).strip()
        key = _norm_key(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out
