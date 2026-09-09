/**
 * Browser implementation of the platform capabilities.
 *
 * The defining difference from the desktop shell: **the API serves this page**. GalaxyHire's API
 * mounts the built UI at `/`, so the backend is same-origin and its port is `location.port` — no
 * discovery protocol, no dynamic port, no restart races. That is why browser-first was chosen: the
 * Tauri sidecar handshake (IPC events, readiness polling, reconnect budgets) exists only to solve
 * a problem the web shell doesn't have.
 */

import type { BackendEndpoint, Platform, UpdateInfo } from './index';

/** Where the API told the page its token is. Fetched once, then cached for the session. */
let tokenPromise: Promise<string | null> | null = null;

/**
 * Fetch the API token from the same-origin bootstrap endpoint.
 *
 * Trust model, stated because it deserves scrutiny: the API binds loopback only, and this endpoint
 * is exempt from bearer auth (it has to be — it's how the page *gets* the bearer). CORS keeps other
 * websites from reading it, so the residual exposure is other processes on this machine, which is
 * the same exposure Tauri's `invoke("get_api_token")` has — a local process can read the token
 * either way. It is not a new hole, but it is the reason the server must never bind 0.0.0.0.
 */
async function fetchToken(): Promise<string | null> {
  try {
    const res = await fetch('/bootstrap', { credentials: 'same-origin', cache: 'no-store' });
    if (!res.ok) return null;
    const body = (await res.json()) as { token?: string };
    return body.token ?? null;
  } catch {
    return null;
  }
}

function currentPort(): number {
  if (typeof window === 'undefined') return 0;
  const explicit = Number(window.location.port);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  // Served behind a proxy on a default port.
  return window.location.protocol === 'https:' ? 443 : 80;
}

export const webPlatform: Platform = {
  kind: 'web',

  async openExternal(url: string): Promise<void> {
    // `noopener` matters: without it the opened job portal gets a `window.opener` handle back into
    // this app, and these URLs come from job postings we don't control.
    window.open(url, '_blank', 'noopener,noreferrer');
  },

  async getVersion(): Promise<string | null> {
    try {
      const res = await fetch('/bootstrap', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) return null;
      const body = (await res.json()) as { version?: string };
      return body.version ?? null;
    } catch {
      return null;
    }
  },

  async notify(title: string, body: string): Promise<void> {
    // Best-effort. Permission is requested lazily rather than on load, so the app doesn't greet a
    // first-time user with a permission prompt they have no context for.
    try {
      if (typeof Notification === 'undefined') return;
      if (Notification.permission === 'granted') {
        new Notification(title, { body });
        return;
      }
      if (Notification.permission === 'default') {
        const granted = await Notification.requestPermission();
        if (granted === 'granted') new Notification(title, { body });
      }
    } catch {
      /* notifications are a nicety, never a failure path */
    }
  },

  async relaunch(): Promise<void> {
    window.location.reload();
  },

  async checkUpdate(): Promise<UpdateInfo> {
    // A browser app has no updater: the user gets the new build on next load. Reporting
    // "unavailable" lets the shared UpdatePrompt render nothing rather than special-casing shells.
    return { available: false };
  },

  watchBackend(onChange: (endpoint: BackendEndpoint | null) => void): () => void {
    let cancelled = false;
    tokenPromise ??= fetchToken();
    void tokenPromise.then(token => {
      if (cancelled) return;
      // A missing token still yields an endpoint: the API may be running without auth in dev, and
      // reporting null here would leave the UI stuck on "connecting" with no explanation.
      onChange({ port: currentPort(), token: token ?? '' });
    });
    return () => {
      cancelled = true;
    };
  },

  async getBackendError(): Promise<string | null> {
    // There is no sidecar to fail. Connection problems surface from the fetch/WebSocket layer.
    return null;
  },

  async pickDirectory(): Promise<string | null> {
    // Browsers deliberately never expose filesystem paths. The File System Access API grants a
    // *handle*, not a path, and the backend needs a path it can write the rendered resume to — so
    // the resume folder is configured server-side in Settings and this returns null. Callers treat
    // null as "keep the configured directory".
    return null;
  },
};
