import { useState, useMemo } from "react";
import type { ProfileEntry, ProfilePoint, ProfileSkill } from "./selectionState";
import { entityTitle, entryPoints } from "./selectionState";
import type { DocSelection } from "../../../api/docPane";

export type Tag = { id: string; name: string };
export type PointTagRow = { point_kind: string; point_id: string; tag_id: string; tag_name: string };

export interface TagIndex {
  // point_id -> tag ids (filtering) / tag names (display pills), reverse of /point-tags.
  idsByPoint: Map<string, Set<string>>;
  namesByPoint: Map<string, string[]>;
}

type TaggedItem = {
  id?: string;
  tag_id?: string;
  tag_ids?: string[];
  source_tag_ids?: string[];
  role_variants?: { tag_id?: string }[];
  title_variants?: { tag_id?: string }[];
};

/** Read provenance from both the normalized point-tags endpoint and newer
 * profile snapshots.  The endpoint remains authoritative, while metadata is
 * the compatibility path for snapshots imported before point-tags existed. */
function itemTagIds(item: TaggedItem | undefined, tagIndex: TagIndex): Set<string> {
  const ids = new Set<string>();
  const id = String(item?.id || "");
  for (const tagId of tagIndex.idsByPoint.get(id) || []) ids.add(tagId);
  if (item?.tag_id) ids.add(String(item.tag_id));
  for (const tagId of item?.tag_ids || []) if (tagId) ids.add(String(tagId));
  for (const tagId of item?.source_tag_ids || []) if (tagId) ids.add(String(tagId));
  for (const variant of [...(item?.role_variants || []), ...(item?.title_variants || [])]) {
    if (variant?.tag_id) ids.add(String(variant.tag_id));
  }
  return ids;
}

function itemTagNames(item: TaggedItem | undefined, tagIndex: TagIndex, tags: Tag[]): string[] {
  const id = String(item?.id || "");
  const names = [...(tagIndex.namesByPoint.get(id) || [])];
  for (const tagId of itemTagIds(item, tagIndex)) {
    const name = tags.find(tag => tag.id === tagId)?.name;
    if (name && !names.some(existing => existing.toLowerCase() === name.toLowerCase())) names.push(name);
  }
  return names;
}

export function buildTagIndex(pointTags: PointTagRow[]): TagIndex {
  const idsByPoint = new Map<string, Set<string>>();
  const namesByPoint = new Map<string, string[]>();
  for (const row of pointTags) {
    const ids = idsByPoint.get(row.point_id) || new Set<string>();
    ids.add(row.tag_id);
    idsByPoint.set(row.point_id, ids);
    const names = namesByPoint.get(row.point_id) || [];
    if (row.tag_name && !names.includes(row.tag_name)) names.push(row.tag_name);
    namesByPoint.set(row.point_id, names);
  }
  return { idsByPoint, namesByPoint };
}

// Track accent colors matching My-Resume-Maker reference
export function getTrackStyle(tagName: string) {
  const norm = tagName.toLowerCase();
  if (norm.includes("sde") || norm.includes("swe") || norm.includes("dev") || norm.includes("backend") || norm.includes("frontend")) {
    return { color: "var(--blue)", bg: "var(--blue-soft)", border: "var(--blue)" };
  }
  if (norm.includes("arch") || norm.includes("infra") || norm.includes("cloud") || norm.includes("ops")) {
    return { color: "var(--purple)", bg: "var(--purple-soft)", border: "var(--purple)" };
  }
  if (norm.includes("ml") || norm.includes("ai") || norm.includes("data") || norm.includes("research")) {
    return { color: "var(--teal)", bg: "var(--teal-soft)", border: "var(--teal)" };
  }
  if (norm.includes("general") || norm.includes("universal") || norm.includes("no tag") || norm.includes("points")) {
    return { color: "var(--yellow-ink, var(--ink-2))", bg: "var(--yellow-soft, var(--paper-3))", border: "var(--yellow, var(--line))" };
  }
  return { color: "var(--ink-2)", bg: "var(--paper-3)", border: "var(--line)" };
}

/** Markdown keyword formatting: converts **bold** and `code` safely into React nodes without dangerouslySetInnerHTML */
export function FormattedText({ text }: { text: string }) {
  const parts = useMemo(() => {
    if (!text) return text;
    const nodes: React.ReactNode[] = [];
    const regex = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
    let lastIdx = 0;
    let match: RegExpExecArray | null;
    let key = 0;
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIdx) {
        nodes.push(text.slice(lastIdx, match.index));
      }
      if (match[2] !== undefined) {
        nodes.push(<b key={`b-${key++}`}>{match[2]}</b>);
      } else if (match[3] !== undefined) {
        nodes.push(
          <code key={`c-${key++}`} className="mono" style={{
            fontSize: "11px", background: "var(--paper-3)", padding: "0 3px", borderRadius: 3, border: "1px solid var(--line)",
          }}>{match[3]}</code>
        );
      }
      lastIdx = regex.lastIndex;
    }
    if (lastIdx < text.length) {
      nodes.push(text.slice(lastIdx));
    }
    return nodes.length ? nodes : text;
  }, [text]);

  return <>{parts}</>;
}

// Visibility-only: chips narrow WHAT YOU SEE, never deselect picked content.
export function TrackChips({ tags, activeTag, onPick }: {
  tags: Tag[]; activeTag: string; onPick: (tagId: string) => void;
}) {
  return (
    <div className="row gap-2" style={{ flexWrap: "wrap", alignItems: "center" }}>
      <span className="eyebrow" style={{ margin: 0 }}>Show track</span>
      {[{ id: "", name: "All" }, ...tags].map(tag => (
        <button key={tag.id || "all"} onClick={() => onPick(tag.id)} style={{
          padding: "2px 9px", borderRadius: 999, fontSize: 10.5, fontWeight: 700, cursor: "pointer",
          border: `1px solid ${activeTag === tag.id ? "var(--blue)" : "var(--line)"}`,
          background: activeTag === tag.id ? "var(--blue-soft)" : "var(--paper)",
          color: activeTag === tag.id ? "var(--blue-ink)" : "var(--ink-3)",
        }}>{tag.name}</button>
      ))}
    </div>
  );
}

export function CompositeTrackBadge({ names }: { names: string[] }) {
  if (names.length <= 1) return null;
  const label = `[${names.slice().sort().join("+")}]`;
  return (
    <span
      className="mono composite-badge"
      title="Also shown in the other column — counted once in the output"
      style={{
        fontSize: 8.5,
        fontWeight: 700,
        color: "var(--blue-ink, var(--ink-3))",
        background: "var(--blue-soft, var(--paper-3))",
        border: "1px solid var(--blue, var(--line))",
        borderRadius: 4,
        padding: "1px 5px",
        whiteSpace: "nowrap",
        letterSpacing: "0.04em",
        flexShrink: 0,
        marginTop: 2,
      }}
    >
      {label}
    </span>
  );
}

export function AltWordingBadge({ note }: { note?: string }) {
  return (
    <span
      className="mono alt-wording-badge"
      title="Alternative wording of the same work — pick one, not both"
      style={{
        fontSize: 8.5,
        fontWeight: 600,
        color: "var(--orange-ink, #c2410c)",
        background: "var(--orange-soft, #ffedd5)",
        border: "1px dashed var(--orange, #f97316)",
        borderRadius: 4,
        padding: "1px 5px",
        whiteSpace: "nowrap",
        letterSpacing: "0.03em",
        flexShrink: 0,
        marginTop: 2,
      }}
    >
      {note ? `[${note}]` : "[Alt wording]"}
    </span>
  );
}

export function SkillsPicker({ skills, selection, onToggle, tagFilter, tagIndex, hiddenIds, onlyUntagged, tags = [] }: {
  skills: ProfileSkill[];
  selection: DocSelection;
  onToggle: (skillId: string) => void;
  tagFilter: string;
  tagIndex: TagIndex;
  /** Display-suppressed ids (conflict dupes / exact-text dupes) — picks persist. */
  hiddenIds?: Set<string>;
  /** Box mode: show only items with no tag at all (the universal box). */
  onlyUntagged?: boolean;
  tags?: Tag[];
}) {
  const isHidden = (id: string) => hiddenIds?.has(id) || false;
  const visible = skills.filter(skill => {
    const id = String(skill.id || "");
    if (isHidden(id)) return false;
    const itemTags = itemTagIds(skill, tagIndex);
    if (onlyUntagged) return itemTags.size === 0;
    if (!tagFilter) return true;
    return itemTags.has(tagFilter);
  });
  const byCat = new Map<string, ProfileSkill[]>();
  for (const skill of visible) {
    const cat = String(skill.cat || skill.category || "").trim() || "Skills";
    byCat.set(cat, [...(byCat.get(cat) || []), skill]);
  }
  if (!byCat.size) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="eyebrow">Skills</div>
      {[...byCat.entries()].map(([cat, items]) => (
        <div key={cat} className="row gap-1" style={{ flexWrap: "wrap", alignItems: "center" }}>
          <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", minWidth: 74 }}>{cat}</span>
          {items.map(skill => {
            const lit = Boolean(skill.id && selection.skills_on.includes(skill.id));
            const names = itemTagNames(skill, tagIndex, tags);
            return (
              <button key={skill.id} onClick={() => skill.id && onToggle(skill.id)} style={{
                padding: "2px 9px", borderRadius: 999, fontSize: 11, fontWeight: 600, cursor: "pointer",
                border: `1px solid ${lit ? "var(--green)" : "var(--line)"}`,
                background: lit ? "var(--green-soft)" : "var(--paper)",
                color: lit ? "var(--green-ink)" : "var(--ink-3)",
                display: "inline-flex", alignItems: "center", gap: 5,
              }}>
                <span>{skill.n}</span>
                {names.length > 0 && !tagFilter && (
                  <span style={{ fontSize: 8.5, opacity: 0.75, fontWeight: 700 }}>
                    {names.join("+")}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export interface PointSuggestion {
  point_id: string;
  parent_kind: string;
  parent_id: string;
  suggested_text: string;
  reason: string;
}

export interface EntriesPickerProps {
  kind: "experience" | "projects";
  entries: ProfileEntry[];
  selection: DocSelection;
  tagFilter: string;
  tagIndex: TagIndex;
  tags?: Tag[];
  onToggleEntry: (entryId: string) => void;
  onTogglePoint: (pointId: string) => void;
  onTogglePoints?: (pointIds: string[], select: boolean) => void;
  onMove: (entryId: string, delta: -1 | 1) => void;
  hiddenIds?: Set<string>;
  onlyUntagged?: boolean;
  altWordings?: Set<string> | Map<string, string>;
  suggestions?: PointSuggestion[];
  onAcceptSuggestion?: (pointId: string) => void;
  onDismissSuggestion?: (pointId: string) => void;
  onEditPoint?: (entryId: string, pointId: string, text: string) => void;
  onAddPoint?: (entryId: string, text: string) => void;
  entityTitles?: Record<string, string>;
  onChooseEntityTitle?: (entryId: string, title: string) => void;
}

function getTrackColumns(
  points: ProfilePoint[],
  tagFilter: string,
  tagIndex: TagIndex,
  tags: Tag[] = [],
) {
  if (tagFilter) {
    const tagName = tags.find(t => t.id === tagFilter)?.name || tagFilter;
    return [{
      key: tagFilter,
      name: tagName,
      points,
      style: getTrackStyle(tagName),
    }];
  }

  const cols: { key: string; name: string; points: ProfilePoint[]; style: ReturnType<typeof getTrackStyle> }[] = [];
  const placedPointIds = new Set<string>();

  // 1. Tags matching provided tags array
  for (const tag of tags) {
    const inTrack = points.filter(p => itemTagIds(p, tagIndex).has(tag.id));
    if (inTrack.length > 0) {
      cols.push({
        key: tag.id,
        name: tag.name,
        points: inTrack,
        style: getTrackStyle(tag.name),
      });
      for (const p of inTrack) placedPointIds.add(p.id);
    }
  }

  // 2. Any point whose tags are not in tags prop but exist in namesByPoint
  for (const p of points) {
    const names = itemTagNames(p, tagIndex, tags);
    if (names.length > 0) {
      for (const name of names) {
        let existing = cols.find(c => c.name.toLowerCase() === name.toLowerCase());
        if (!existing) {
          existing = { key: name.toLowerCase(), name, points: [], style: getTrackStyle(name) };
          cols.push(existing);
        }
        if (!existing.points.some(q => q.id === p.id)) {
          existing.points.push(p);
        }
        placedPointIds.add(p.id);
      }
    }
  }

  // 3. Untagged / universal points
  const untagged = points.filter(p => !placedPointIds.has(p.id));
  if (untagged.length > 0) {
    cols.push({
      key: "__general",
      name: "General",
      points: untagged,
      style: getTrackStyle("General"),
    });
  }

  // 4. Fallback if no columns created but points exist
  if (cols.length === 0 && points.length > 0) {
    cols.push({
      key: "all",
      name: "General",
      points,
      style: getTrackStyle("General"),
    });
  }

  return cols;
}

export function EntriesPicker({
  kind,
  entries,
  selection,
  tagFilter,
  tagIndex,
  tags = [],
  onToggleEntry,
  onTogglePoint,
  onTogglePoints,
  onMove,
  hiddenIds,
  onlyUntagged,
  altWordings,
  suggestions,
  onAcceptSuggestion,
  onDismissSuggestion,
  onEditPoint,
  onAddPoint,
  entityTitles = {},
  onChooseEntityTitle,
}: EntriesPickerProps) {
  const label = kind === "experience" ? "Experience" : "Projects";
  const onKey = kind === "experience" ? "experience_on" : "projects_on";
  const [editing, setEditing] = useState<{ entryId: string; pointId: string; text: string } | null>(null);
  const [adding, setAdding] = useState<{ entryId: string; text: string } | null>(null);

  const rowVisible = (rowId: string, item?: TaggedItem, parent?: TaggedItem): boolean => {
    if (hiddenIds?.has(rowId)) return false;
    const ownTags = itemTagIds(item || { id: rowId }, tagIndex);
    const itemTags = ownTags.size > 0 ? ownTags : itemTagIds(parent, tagIndex);
    if (onlyUntagged) return itemTags.size === 0;
    if (!tagFilter) return true;
    return itemTags.has(tagFilter);
  };

  const visible = entries.filter(entry =>
    entryPoints(entry).some(point => rowVisible(point.id, point, entry)) || rowVisible(String(entry.id || ""), entry));
  if (!visible.length) return null;

  const suggestionByPoint = new Map((suggestions || []).map(s => [s.point_id, s]));

  const handleBulkToggle = (pointIds: string[], targetOn: boolean) => {
    if (onTogglePoints) {
      onTogglePoints(pointIds, targetOn);
    } else {
      for (const id of pointIds) {
        const isCurrentlyOn = selection.points_on.includes(id);
        if (targetOn && !isCurrentlyOn) onTogglePoint(id);
        else if (!targetOn && isCurrentlyOn) onTogglePoint(id);
      }
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="eyebrow">{label}</div>
      {visible.map(entry => {
        const entryId = String(entry.id || "");
        const rawVariants = kind === "experience" ? entry.role_variants || [] : entry.title_variants || [];
        const titleVariants = Array.from(new Set(
          rawVariants.map(variant => String(variant.title || "").trim()).filter(Boolean),
        ));
        const defaultTitle = entityTitle(entry, { entity_titles: {} });
        if (!titleVariants.includes(defaultTitle)) titleVariants.unshift(defaultTitle);
        const displayedTitle = entityTitle(entry, { entity_titles: entityTitles });
        const entryOn = selection[onKey].includes(entryId);
        const points = entryPoints(entry).filter(p => rowVisible(p.id, p, entry));
        const pickedCount = points.filter(p => selection.points_on.includes(p.id)).length;
        const orderIdx = (kind === "experience" ? selection.experience_order : selection.projects_order).indexOf(entryId);

        const trackColumns = getTrackColumns(points, tagFilter, tagIndex, tags);
        const colCount = Math.max(1, trackColumns.length);

        return (
          <div key={entryId} className="card" style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px", background: "var(--paper)" }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <label className="row" style={{ alignItems: "center", gap: 8, cursor: "pointer", minWidth: 0 }}>
                <input type="checkbox" checked={entryOn} onChange={() => onToggleEntry(entryId)} style={{ accentColor: "var(--blue)" }} />
                <span style={{ fontSize: 13, fontWeight: 700, overflowWrap: "anywhere", color: entryOn ? "var(--ink)" : "var(--ink-3)" }}>
                  {displayedTitle}
                  {entry.co ? <span style={{ fontWeight: 500, color: "var(--ink-2)" }}> · {String(entry.co)}</span> : null}
                  {entry.period ? <span style={{ fontWeight: 400, color: "var(--ink-3)", fontSize: 11.5 }}> · {String(entry.period)}</span> : null}
                </span>
              </label>
              <div className="row" style={{ gap: 4, alignItems: "center", flexShrink: 0 }}>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginRight: 4 }}>
                  {pickedCount}/{points.length} picked
                </span>
                <button
                  title="Move up"
                  disabled={orderIdx <= 0}
                  onClick={() => onMove(entryId, -1)}
                  style={{
                    padding: "1px 7px", fontSize: 11, cursor: orderIdx <= 0 ? "default" : "pointer",
                    border: "1px solid var(--line)", background: "var(--paper-2)", borderRadius: 4,
                    opacity: orderIdx <= 0 ? 0.35 : 1,
                  }}
                >
                  &uarr;
                </button>
                <button
                  title="Move down"
                  disabled={orderIdx >= 0 && orderIdx >= selection[kind === "experience" ? "experience_order" : "projects_order"].length - 1}
                  onClick={() => onMove(entryId, 1)}
                  style={{
                    padding: "1px 7px", fontSize: 11, cursor: "pointer",
                    border: "1px solid var(--line)", background: "var(--paper-2)", borderRadius: 4,
                    opacity: orderIdx >= 0 && orderIdx >= selection[kind === "experience" ? "experience_order" : "projects_order"].length - 1 ? 0.35 : 1,
                  }}
                >
                  &darr;
                </button>
              </div>
            </div>

            {titleVariants.length > 1 && (
              <div className="row gap-2" role="radiogroup" aria-label={`${label} title choices`} style={{ marginTop: 7, marginLeft: 24, flexWrap: "wrap", alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", textTransform: "uppercase" }}>
                  Resume title
                </span>
                {titleVariants.map(title => (
                  <button
                    key={title}
                    type="button"
                    className="btn"
                    aria-pressed={displayedTitle === title}
                    role="radio"
                    aria-checked={displayedTitle === title}
                    onClick={() => onChooseEntityTitle?.(entryId, title)}
                    style={{
                      fontSize: 10.5,
                      padding: "2px 8px",
                      background: displayedTitle === title ? "var(--blue-soft)" : "var(--paper-2)",
                      borderColor: displayedTitle === title ? "var(--blue)" : "var(--line)",
                    }}
                  >
                    {title}
                  </button>
                ))}
              </div>
            )}

            {/* Stack line for project */}
            {entry.stack ? (
              <div style={{ fontSize: 11.5, color: "var(--ink-3)", fontStyle: "italic", marginTop: 2, marginLeft: 24 }}>
                {String(entry.stack)}
              </div>
            ) : null}

            {points.length > 0 && (
              <div style={{ marginTop: 10, marginLeft: 22 }}>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))`,
                    gap: 10,
                    alignItems: "start",
                  }}
                >
                  {trackColumns.map(col => {
                    const allOn = col.points.length > 0 && col.points.every(p => selection.points_on.includes(p.id));

                    return (
                      <div
                        key={col.key}
                        style={{
                          border: "1px solid var(--line)",
                          borderRadius: 6,
                          overflow: "hidden",
                          background: "var(--paper-2)",
                          minWidth: 0,
                        }}
                      >
                        {/* Track Column Header with Accent Style and Bulk all/none Toggle */}
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: 6,
                            padding: "5px 8px",
                            fontSize: 10.5,
                            fontWeight: 700,
                            letterSpacing: "0.06em",
                            borderBottom: "1px solid var(--line)",
                            background: col.style.bg,
                            color: col.style.color,
                          }}
                        >
                          <span style={{ textTransform: "uppercase" }}>{col.name}</span>
                          <button
                            type="button"
                            className="btn btn-ghost"
                            onClick={() => handleBulkToggle(col.points.map(p => p.id), !allOn)}
                            title={allOn ? `Unselect all points in ${col.name}` : `Select all points in ${col.name}`}
                            style={{
                              fontSize: 10,
                              fontWeight: 700,
                              padding: "1px 6px",
                              border: "1px solid var(--line)",
                              borderRadius: 4,
                              background: "var(--paper)",
                              color: "var(--ink-2)",
                              cursor: "pointer",
                              lineHeight: 1.2,
                            }}
                          >
                            {allOn ? "none" : "all"}
                          </button>
                        </div>

                        {/* Bullets inside Column */}
                        <div style={{ display: "flex", flexDirection: "column" }}>
                          {col.points.map((point, idx) => {
                            const on = selection.points_on.includes(point.id);
                            const sug = suggestionByPoint.get(point.id);
                            const isEditing = editing?.pointId === point.id;
                            const tagNames = itemTagNames(point, tagIndex, tags);
                            const isStarred = Boolean(point.star);
                            const isAlt = Boolean(
                              point.note ||
                              (altWordings instanceof Set ? altWordings.has(point.id) : (altWordings instanceof Map ? altWordings.has(point.id) : false))
                            );
                            const altNote = point.note || (altWordings instanceof Map ? altWordings.get(point.id) : undefined);

                            return (
                              <div
                                key={point.id}
                                style={{
                                  padding: "6px 8px",
                                  borderTop: idx > 0 ? "1px solid var(--line)" : "none",
                                  background: on ? "var(--paper)" : "transparent",
                                  transition: "background 0.1s ease",
                                }}
                              >
                                <label className="row" style={{ alignItems: "flex-start", gap: 6, cursor: "pointer" }}>
                                  <input
                                    type="checkbox"
                                    checked={on}
                                    onChange={() => onTogglePoint(point.id)}
                                    style={{ marginTop: 3, flexShrink: 0, accentColor: "var(--blue)", cursor: "pointer" }}
                                  />
                                  {isStarred && (
                                    <span title="Strongest line in this block" style={{ color: "var(--orange, #f59e0b)", fontSize: 11, flexShrink: 0, marginTop: 1 }}>
                                      ★
                                    </span>
                                  )}
                                  {tagNames.length > 1 && <CompositeTrackBadge names={tagNames} />}
                                  {isAlt && <AltWordingBadge note={altNote} />}

                                  {isEditing ? (
                                    <span style={{ flex: 1, minWidth: 0 }} onClick={e => e.preventDefault()}>
                                      <textarea
                                        value={editing.text}
                                        onChange={e => setEditing({ ...editing, text: e.target.value })}
                                        rows={3}
                                        autoFocus
                                        style={{ width: "100%", fontSize: 12, padding: "4px 6px", borderRadius: 4, border: "1px solid var(--blue)" }}
                                      />
                                      <span className="row gap-2" style={{ marginTop: 4 }}>
                                        <button
                                          className="btn btn-primary"
                                          style={{ fontSize: 10.5, padding: "2px 8px" }}
                                          onClick={() => { onEditPoint?.(entryId, point.id, editing.text); setEditing(null); }}
                                        >
                                          Save
                                        </button>
                                        <button
                                          className="btn btn-ghost"
                                          style={{ fontSize: 10.5, padding: "2px 8px" }}
                                          onClick={() => setEditing(null)}
                                        >
                                          Cancel
                                        </button>
                                      </span>
                                    </span>
                                  ) : (
                                    <span
                                      style={{
                                        fontSize: 12,
                                        lineHeight: 1.45,
                                        color: on ? "var(--ink)" : "var(--ink-3)",
                                        overflowWrap: "anywhere",
                                        flex: 1,
                                      }}
                                      title="Double-click to edit this point"
                                      onDoubleClick={e => { e.preventDefault(); setEditing({ entryId, pointId: point.id, text: point.text }); }}
                                    >
                                      <FormattedText text={point.text} />
                                    </span>
                                  )}

                                  {!isEditing && onEditPoint && (
                                    <button
                                      type="button"
                                      title="Edit point"
                                      onClick={e => { e.preventDefault(); setEditing({ entryId, pointId: point.id, text: point.text }); }}
                                      style={{
                                        border: "none", background: "none", cursor: "pointer",
                                        color: "var(--ink-4)", fontSize: 11, padding: "0 2px", flexShrink: 0,
                                      }}
                                    >
                                      ✎
                                    </button>
                                  )}
                                </label>

                                {sug && (
                                  <div style={{
                                    marginTop: 6, marginLeft: 20, padding: "6px 8px", borderRadius: 6,
                                    border: "1px dashed var(--purple)", background: "var(--purple-soft)",
                                  }}>
                                    <div className="mono" style={{ fontSize: 9, fontWeight: 800, color: "var(--purple-ink)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>
                                      AI suggestion — {sug.reason}
                                    </div>
                                    <div style={{ fontSize: 11.5, lineHeight: 1.4, color: "var(--ink)" }}>{sug.suggested_text}</div>
                                    <div className="row gap-2" style={{ marginTop: 4 }}>
                                      <button className="btn btn-primary" style={{ fontSize: 10, padding: "2px 6px" }} onClick={() => onAcceptSuggestion?.(point.id)}>
                                        Use this wording
                                      </button>
                                      <button className="btn btn-ghost" style={{ fontSize: 10, padding: "2px 6px" }} onClick={() => onDismissSuggestion?.(point.id)}>
                                        Dismiss
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {onAddPoint && (
                  adding?.entryId === entryId ? (
                    <div className="row gap-2" style={{ marginTop: 8 }}>
                      <input
                        value={adding.text}
                        onChange={e => setAdding({ entryId, text: e.target.value })}
                        placeholder="New custom point…"
                        autoFocus
                        className="field-input"
                        style={{ flex: 1, fontSize: 12, padding: "4px 8px" }}
                        onKeyDown={e => {
                          if (e.key === "Enter" && adding.text.trim()) { onAddPoint(entryId, adding.text.trim()); setAdding(null); }
                          if (e.key === "Escape") setAdding(null);
                        }}
                      />
                      <button
                        className="btn btn-primary"
                        style={{ fontSize: 11, padding: "4px 10px" }}
                        disabled={!adding.text.trim()}
                        onClick={() => { onAddPoint(entryId, adding.text.trim()); setAdding(null); }}
                      >
                        Add
                      </button>
                      <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }} onClick={() => setAdding(null)}>
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      className="btn btn-ghost"
                      style={{ fontSize: 11, padding: "3px 9px", marginTop: 8 }}
                      onClick={() => setAdding({ entryId, text: "" })}
                    >
                      + Add point
                    </button>
                  )
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
