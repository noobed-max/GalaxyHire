import { platform } from "./platform";

/**
 * Open a web URL in the user's default browser, but only if it is http(s).
 *
 * Lead URLs come from job postings (attacker-controlled), so a crafted `file:`, `javascript:`, or
 * other non-web scheme must never reach the OS opener or `window.open`. The scheme check stays here
 * rather than in the platform layer precisely so that neither shell can be reached without it.
 *
 * Internal `blob:`/`data:` URLs we generate ourselves must use {@link openGeneratedDocument}
 * instead — they are legitimate but would (correctly) fail this check.
 */
export async function openExternalUrl(url: string | null | undefined): Promise<void> {
  if (!url) return;
  let scheme: string;
  try {
    scheme = new URL(url).protocol;
  } catch {
    console.warn("[openExternal] refusing to open malformed URL:", url);
    return;
  }
  if (scheme !== "http:" && scheme !== "https:") {
    console.warn("[openExternal] refusing to open non-http(s) URL:", url);
    return;
  }
  await (await platform()).openExternal(url);
}

/**
 * Open a document this app generated — a rendered resume or cover letter held as a `blob:` URL.
 *
 * Separate from {@link openExternalUrl} on purpose. Those two callers pass URLs we minted ourselves
 * from our own bytes, so the http(s) restriction is wrong for them: routing a `blob:` URL through
 * that guard silently refuses to open the user's own résumé, with only a console warning to show
 * for it. Keeping them as distinct functions means neither one's rules can quietly be applied to
 * the other's inputs.
 *
 * Still restricted, just to a different set: only schemes we can actually have produced.
 */
export async function openGeneratedDocument(url: string | null | undefined): Promise<void> {
  if (!url) return;
  let scheme: string;
  try {
    scheme = new URL(url).protocol;
  } catch {
    console.warn("[openExternal] refusing to open malformed document URL:", url);
    return;
  }
  if (scheme !== "blob:" && scheme !== "data:") {
    console.warn("[openExternal] not a generated document URL:", url);
    return;
  }
  await (await platform()).openExternal(url);
}
