import { DEFAULT_SECTIONS, entityTitle, entryPoints, pickedEntries, pickedSkills,
  type ProfileData } from "./selectionState";
import type { DocSelection } from "../../../api/docPane";

// Client-side assembly of what the generator was told to reproduce. Plain mono
// block on purpose: it is a content contract preview, not a layout mockup.
export function buildPreviewText(profile: ProfileData, selection: DocSelection): string {
  const lines: string[] = [];
  const sections = selection.sections?.length ? selection.sections : DEFAULT_SECTIONS;
  for (const section of sections) {
    if (section === "skills") {
      const skills = pickedSkills(profile, selection).map(s => s.n || "").filter(Boolean);
      if (skills.length) lines.push("## SKILLS", skills.join(", "), "");
    } else if (section === "experience" || section === "projects") {
      const rows = pickedEntries(profile, selection, section);
      if (!rows.length) continue;
      lines.push(`## ${section.toUpperCase()}`);
      for (const row of rows) {
        const role = entityTitle(row, selection);
        const co = String(row.co || "");
        const title = String(row.title || "");
        const head = [role && co ? `${role} - ${co}` : role || co || title, String(row.period || "")]
          .filter(Boolean).join(" ");
        lines.push(`### ${head || title || "Entry"}`);
        const points = entryPoints(row).filter(p => selection.points_on.includes(p.id));
        const visible = points.length ? points : entryPoints(row);
        for (const p of visible) lines.push(`- ${p.text}`);
        lines.push("");
      }
    }
  }
  return lines.join("\n").trim() || "Pick some points to see the resume take shape.";
}

export function PreviewPane({ profile, selection }: { profile: ProfileData; selection: DocSelection }) {
  return (
    <pre
      className="mono"
      style={{
        margin: 0, padding: 12, background: "var(--card)", border: "1px solid var(--line)",
        borderRadius: 10, fontSize: 10.5, lineHeight: 1.5, color: "var(--ink-2)",
        whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 320, overflowY: "auto",
      }}
    >{buildPreviewText(profile, selection)}</pre>
  );
}
