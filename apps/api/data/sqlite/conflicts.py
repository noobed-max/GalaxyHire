from __future__ import annotations

import uuid

from .connection import get_connection, DEFAULT_DB_PATH


def _gid() -> str:
    return "cg" + uuid.uuid4().hex[:10]


def _require(conn, group_id: str) -> None:
    row = conn.execute("SELECT id FROM conflict_groups WHERE id = ?", (group_id,)).fetchone()
    if not row:
        raise ValueError(f"conflict group not found: {group_id}")


def list_groups(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """All groups with their member lists attached (no texts — enrichment is the
    router's job, resolved live against the current profile)."""
    conn = get_connection(db_path)
    try:
        groups = [dict(row) for row in conn.execute(
            "SELECT id, reason, score, status, picked_kind, picked_id, created_at "
            "FROM conflict_groups ORDER BY created_at DESC, id"
        ).fetchall()]
        members: dict[str, list[dict]] = {}
        for row in conn.execute(
            "SELECT group_id, point_kind, point_id FROM conflict_members "
            "ORDER BY point_kind, point_id"
        ).fetchall():
            m = dict(row)
            members.setdefault(m["group_id"], []).append(
                {"point_kind": m["point_kind"], "point_id": m["point_id"]}
            )
    finally:
        conn.close()
    for group in groups:
        group["members"] = members.get(group["id"], [])
    return groups


def create_group(reason: str, score: float, members: list[tuple[str, str]],
                 db_path: str = DEFAULT_DB_PATH) -> dict:
    """Create a group over member (point_kind, point_id) pairs. Members already
    in another group are MOVED here (union-merge): a new observation bridging
    two existing groups folds them into one. Raises ValueError when fewer than
    two distinct members remain — a lone point is not a conflict."""
    clean: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, pid in members or []:
        pair = (str(kind or "").strip(), str(pid or "").strip())
        if pair[0] and pair[1] and pair not in seen:
            seen.add(pair)
            clean.append(pair)
    if len(clean) < 2:
        raise ValueError("a conflict group needs at least two distinct members")

    conn = get_connection(db_path)
    try:
        group_id = _gid()
        conn.execute("BEGIN")
        # Union-merge: every group these members already belong to is absorbed.
        absorbed: set[str] = set()
        extra: set[tuple[str, str]] = set()
        for kind, pid in clean:
            rows = conn.execute(
                "SELECT group_id FROM conflict_members WHERE point_kind = ? AND point_id = ?",
                (kind, pid),
            ).fetchall()
            for row in rows:
                absorbed.add(row["group_id"])
        for old_id in absorbed:
            for row in conn.execute(
                "SELECT point_kind, point_id FROM conflict_members WHERE group_id = ?",
                (old_id,),
            ).fetchall():
                extra.add((row["point_kind"], row["point_id"]))
        all_members = list(dict.fromkeys([*clean, *extra]))

        if absorbed:
            marks = ",".join("?" for _ in absorbed)
            conn.execute(f"DELETE FROM conflict_members WHERE group_id IN ({marks})", tuple(absorbed))
            conn.execute(f"DELETE FROM conflict_groups WHERE id IN ({marks})", tuple(absorbed))
        conn.execute(
            "INSERT INTO conflict_groups(id, reason, score, status) VALUES(?,?,?,'open')",
            (group_id, reason, float(score)),
        )
        for kind, pid in all_members:
            conn.execute(
                "INSERT OR IGNORE INTO conflict_members(group_id, point_kind, point_id) VALUES(?,?,?)",
                (group_id, kind, pid),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT point_kind, point_id FROM conflict_members WHERE group_id = ? "
            "ORDER BY point_kind, point_id",
            (group_id,),
        ).fetchall()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "id": group_id, "reason": reason, "score": float(score), "status": "open",
        "picked_kind": None, "picked_id": None,
        "members": [{"point_kind": r["point_kind"], "point_id": r["point_id"]} for r in rows],
    }


def resolve(group_id: str, point_kind: str, point_id: str,
            db_path: str = DEFAULT_DB_PATH) -> dict:
    """Pick one member as THE wording for future builds. The member must belong
    to the group."""
    conn = get_connection(db_path)
    try:
        _require(conn, group_id)
        member = conn.execute(
            "SELECT 1 FROM conflict_members WHERE group_id = ? AND point_kind = ? AND point_id = ?",
            (group_id, point_kind, point_id),
        ).fetchone()
        if not member:
            raise ValueError(f"point ({point_kind}, {point_id}) is not a member of {group_id}")
        conn.execute(
            "UPDATE conflict_groups SET status='picked', picked_kind=?, picked_id=? WHERE id=?",
            (point_kind, point_id, group_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": group_id, "status": "picked", "picked_kind": point_kind, "picked_id": point_id}


def keep_both(group_id: str, db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = get_connection(db_path)
    try:
        _require(conn, group_id)
        conn.execute(
            "UPDATE conflict_groups SET status='keep_both', picked_kind=NULL, picked_id=NULL WHERE id=?",
            (group_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": group_id, "status": "keep_both", "picked_id": None}


def dismiss(group_id: str, db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = get_connection(db_path)
    try:
        _require(conn, group_id)
        conn.execute("UPDATE conflict_groups SET status='dismissed' WHERE id=?", (group_id,))
        conn.commit()
    finally:
        conn.close()
    return {"id": group_id, "status": "dismissed"}


def reopen(group_id: str, db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = get_connection(db_path)
    try:
        _require(conn, group_id)
        conn.execute(
            "UPDATE conflict_groups SET status='open', picked_kind=NULL, picked_id=NULL WHERE id=?",
            (group_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": group_id, "status": "open", "picked_kind": None}


def _purge_underpopulated(conn) -> None:
    """Delete groups with <2 members — AND their member rows (FK enforcement
    is off, so deleting a group otherwise orphans its remaining memberships,
    which would resurface as phantom stale rows in every later prune)."""
    conn.execute(
        "DELETE FROM conflict_members WHERE group_id IN ("
        "  SELECT group_id FROM conflict_members"
        "  GROUP BY group_id HAVING count(*) < 2"
        ") OR group_id NOT IN (SELECT DISTINCT group_id FROM conflict_members)"
    )
    conn.execute(
        "DELETE FROM conflict_groups WHERE id NOT IN "
        "(SELECT DISTINCT group_id FROM conflict_members)"
    )


def delete_for_point(point_kind: str, point_id: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Remove a vanished point from every group; groups left with fewer than
    two members are deleted along with their member rows (a lone point is not
    a conflict; cascade is inert — FK enforcement is off)."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE conflict_groups SET status='open', picked_kind=NULL, picked_id=NULL "
            "WHERE picked_kind = ? AND picked_id = ?",
            (point_kind, point_id),
        )
        conn.execute(
            "DELETE FROM conflict_members WHERE point_kind = ? AND point_id = ?",
            (point_kind, point_id),
        )
        _purge_underpopulated(conn)
        conn.commit()
    finally:
        conn.close()


def prune_stale(valid_pairs: set[tuple[str, str]], db_path: str = DEFAULT_DB_PATH) -> dict:
    """Drop memberships whose point no longer exists in the profile; delete
    groups left with fewer than two members. Returns {members_removed: N}."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT group_id, point_kind, point_id FROM conflict_members").fetchall()
        stale = [
            (r["group_id"], r["point_kind"], r["point_id"])
            for r in rows
            if (r["point_kind"], r["point_id"]) not in valid_pairs
        ]
        for group_id, kind, pid in stale:
            conn.execute(
                "DELETE FROM conflict_members WHERE group_id=? AND point_kind=? AND point_id=?",
                (group_id, kind, pid),
            )
            # A legacy picked entity row (or a deleted bullet) must not leave a
            # dangling selection after its membership is pruned.  Reopen the
            # surviving group so generation falls back to deterministic order.
            conn.execute(
                "UPDATE conflict_groups SET status='open', picked_kind=NULL, picked_id=NULL "
                "WHERE id=? AND picked_kind=? AND picked_id=?",
                (group_id, kind, pid),
            )
        _purge_underpopulated(conn)
        conn.commit()
        return {"members_removed": len(stale)}
    finally:
        conn.close()


def active_groups(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """Groups that constrain builds: status open or picked (keep_both and
    dismissed never exclude anything)."""
    return [g for g in list_groups(db_path) if g["status"] in ("open", "picked")]
