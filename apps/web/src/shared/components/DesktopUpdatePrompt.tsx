import { lazy, Suspense } from "react";

import { isTauri } from "../lib/platform";

/**
 * Renders the auto-updater prompt on the desktop, nothing in a browser.
 *
 * `UpdatePrompt` is deliberately *not* rewritten against the platform abstraction. It is 246 lines
 * built on Tauri's updater: install-location eligibility checks, signed-download headers, granular
 * progress events, relaunch-into-update. Flattening that onto a two-shell interface would degrade
 * the desktop update experience, which is the opposite of what D3 asks for — the desktop
 * experience is meant to be replicated, not levelled down.
 *
 * A browser has no installer to update; the user gets the new build on reload. So the right answer
 * is not a web implementation, it is not rendering.
 *
 * `lazy` matters as much as the conditional: it keeps `@tauri-apps/plugin-updater` out of the web
 * bundle entirely rather than shipping code that merely never runs.
 */
const UpdatePrompt = lazy(() =>
  import("./UpdatePrompt").then(m => ({ default: m.UpdatePrompt })),
);

export function DesktopUpdatePrompt() {
  if (!isTauri()) return null;
  return (
    <Suspense fallback={null}>
      <UpdatePrompt />
    </Suspense>
  );
}
