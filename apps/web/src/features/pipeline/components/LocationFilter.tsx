import { useEffect, useRef, useState } from "react";

import { discoveryApi, type LocationSuggestion } from "../../../api/discovery";
import type { ApiFetch } from "../../../types";

/**
 * Free-text location box with suggestions drawn from the corpus.
 *
 * Suggestions come from the stored jobs rather than a country list, so every option demonstrably
 * has jobs behind it — a curated list would happily offer "Germany" for a corpus containing none.
 * Each row shows its job count for the same reason: "India · 81 jobs" tells you whether narrowing
 * to it is worth doing before you click.
 *
 * Free text is still accepted verbatim. The suggestions are a shortcut, not a whitelist, because
 * the underlying data is inconsistent enough (`country` holds both "United States" and bare state
 * codes) that a closed vocabulary would exclude real places.
 */
export function LocationFilter({ value, onChange, api, suggestions: scopedSuggestions }: {
  value: string;
  onChange: (v: string) => void;
  api?: ApiFetch | null;
  suggestions?: LocationSuggestion[];
}) {
  const [suggestions, setSuggestions] = useState<LocationSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    if (Array.isArray(scopedSuggestions)) {
      const term = value.trim().toLowerCase();
      setSuggestions(
        term.length < 2
          ? []
          : scopedSuggestions
              .filter(row => row.label.toLowerCase().includes(term))
              .slice(0, 12),
      );
      return;
    }
    if (!api || value.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    // Debounced, and guarded against out-of-order responses: typing fast fires several requests and
    // a slow earlier one must not overwrite the results for what is now on screen.
    const mine = ++seq.current;
    const timer = window.setTimeout(() => {
      discoveryApi
        .locations(api, value.trim())
        .then(rows => {
          if (mine === seq.current) setSuggestions(rows);
        })
        .catch(() => {
          if (mine === seq.current) setSuggestions([]);
        });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [value, api, scopedSuggestions]);

  return (
    <label className="pipeline-field" style={{ position: "relative" }}>
      <span>Location</span>
      <input
        type="text"
        value={value}
        placeholder="Anywhere"
        onChange={e => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        // Delayed so a click on a suggestion registers before the list unmounts.
        onBlur={() => window.setTimeout(() => setOpen(false), 160)}
        style={{ minWidth: 150 }}
      />
      {open && suggestions.length > 0 && (
        <div
          role="listbox"
          style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 40,
            background: "var(--card)", border: "1px solid var(--line)", borderRadius: 8,
            marginTop: 4, maxHeight: 260, overflowY: "auto",
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
          }}
        >
          {suggestions.map(s => (
            <button
              key={s.label}
              type="button"
              role="option"
              // onMouseDown, not onClick: blur fires first otherwise and the list is gone.
              onMouseDown={e => { e.preventDefault(); onChange(s.label); setOpen(false); }}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                width: "100%", padding: "6px 10px", border: "none", background: "transparent",
                color: "var(--ink)", cursor: "pointer", fontSize: 12, textAlign: "left",
              }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--paper-3)")}
              onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
            >
              <span>{s.label}</span>
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>{s.jobs}</span>
            </button>
          ))}
        </div>
      )}
    </label>
  );
}
