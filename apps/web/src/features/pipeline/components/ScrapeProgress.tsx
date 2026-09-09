import { useCallback, useEffect, useRef, useState } from "react";

import { discoveryApi, type ScrapeState } from "../../../api/discovery";
import type { ApiFetch } from "../../../types";

/** How often to ask the corpus how the scrape is going. */
const POLL_MS = 2500;

/**
 * Live scrape indicator: what is being collected, how much so far, and how long it has taken.
 *
 * The state comes from the server on every poll rather than being held here, which is what makes a
 * refresh work. The user asked for exactly that — "refreshing the page should be able to dynamically
 * detect the state of scraper etc you know like save states" — and it is not achievable with React
 * state, which dies with the tab, or with the in-memory task registry, which dies with the process.
 * Mounting this component fresh after a reload picks up a scrape already in flight, because the
 * first poll returns it.
 *
 * There is no percentage bar on purpose. Total yield is not knowable in advance — it depends on
 * which of the selected portals have anything for the phrase — so a percentage would be invented.
 * A count that only goes up is honest and just as reassuring.
 */
export function ScrapeProgress({
  api,
  onStop,
}: {
  api?: ApiFetch | null;
  onStop?: () => void;
}) {
  const [state, setState] = useState<ScrapeState | null>(null);
  const [showTerminal, setShowTerminal] = useState(false);
  const observedRun = useRef<number | null>(null);

  const poll = useCallback(async () => {
    if (!api) return;
    try {
      const next = await discoveryApi.scrapeStatus(api);
      if (next.running) {
        observedRun.current = next.run_id;
        setShowTerminal(true);
      }
      setState(next);
    } catch {
      // Swallowed deliberately: this runs every couple of seconds, and a transient blip must not
      // put an error in front of someone who is just waiting for jobs.
    }
  }, [api]);

  useEffect(() => {
    poll();
    // Keep polling while running so the "finished" transition is picked up too; the interval is
    // cheap and stopping early would leave the banner claiming a scrape is still going.
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [poll]);

  useEffect(() => {
    if (!state || state.running || !showTerminal || observedRun.current !== state.run_id) return;
    const timer = window.setTimeout(() => setShowTerminal(false), 12000);
    return () => window.clearTimeout(timer);
  }, [showTerminal, state]);

  // A completed row is retained by the corpus for freshness accounting, but it is history—not
  // current UI state. Only show a terminal result when this mounted page actually observed that
  // run in progress, and dismiss it shortly afterwards. A refresh or data reset therefore cannot
  // resurrect an old résumé-derived phrase as if it had just run.
  if (!state || state.status === "idle" || (!state.running && !showTerminal)) return null;

  const running = state.running;
  const failed = state.status === "failed";
  const tone = failed ? "bad" : running ? "green" : "blue";
  const elapsed = state.elapsed_seconds >= 60
    ? `${Math.floor(state.elapsed_seconds / 60)}m ${Math.round(state.elapsed_seconds % 60)}s`
    : `${Math.round(state.elapsed_seconds)}s`;

  const headline = running
    ? `Scraping “${state.phrase}” across ${state.portals?.length ?? "the selected"} portal${state.portals?.length === 1 ? "" : "s"}`
    : failed
      ? `Scrape of “${state.phrase}” failed`
      : state.status === "stopped"
        ? `Scrape of “${state.phrase}” stopped`
        : `Finished scraping “${state.phrase}”`;

  return (
    <div
      className="card"
      style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
        border: `1px solid var(--${tone})`, background: `var(--${tone}-soft)`, marginBottom: 12,
      }}
    >
      {/* The "glowing green thing" — animation only while actually collecting, so a still dot
          genuinely means stopped rather than merely being decorative. */}
      <span
        aria-hidden
        style={{
          width: 9, height: 9, borderRadius: 999, flexShrink: 0,
          background: `var(--${tone}-ink)`,
          animation: running ? "pulse-soft 1.2s ease-in-out infinite" : "none",
          boxShadow: running ? `0 0 8px var(--${tone}-ink)` : "none",
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: `var(--${tone}-ink)` }}>{headline}</div>
        <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 2 }}>
          {state.collected} job{state.collected === 1 ? "" : "s"} collected · {elapsed}
          {running && " · filters apply once it finishes"}
        </div>
        {failed && state.error && (
          <div style={{ fontSize: 10.5, color: "var(--bad)", marginTop: 3, whiteSpace: "pre-wrap" }}>
            {state.error.slice(0, 300)}
          </div>
        )}
      </div>
      {running && api && (
        <button
          className="btn"
          onClick={() => {
            if (onStop) onStop();
            else discoveryApi.scrapeStop(api).then(poll).catch(() => {});
          }}
        >Stop</button>
      )}
      {!running && (
        <button className="btn" onClick={() => setShowTerminal(false)}>Dismiss</button>
      )}
    </div>
  );
}
