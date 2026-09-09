/**
 * Tauri implementation of the platform capabilities — the desktop experience, preserved.
 *
 * Everything here is behind dynamic `import()`. This module is only ever loaded after `isTauri()`
 * returns true, but the inner imports stay lazy too so that a bundler analysing the web build never
 * has to resolve `@tauri-apps/*` at all.
 *
 * The sidecar handshake is the substantial part. Tauri spawns the Python API as a child process on
 * a dynamic port and announces port/token/errors over IPC events. Consequences the web shell
 * doesn't have to deal with: the endpoint is unknown at startup, it can change mid-session if the
 * sidecar restarts, and events can arrive before or after the initial `invoke` calls — so this both
 * polls *and* subscribes, and treats whichever arrives first as authoritative.
 */

import type { BackendEndpoint, Platform, UpdateInfo } from './index';

export const tauriPlatform: Platform = {
  kind: 'tauri',

  async openExternal(url: string): Promise<void> {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl(url);
  },

  async getVersion(): Promise<string | null> {
    try {
      const { getVersion } = await import('@tauri-apps/api/app');
      return await getVersion();
    } catch {
      return null;
    }
  },

  async notify(title: string, body: string): Promise<void> {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      // A Rust command rather than the notification plugin: the desktop build already exposes this
      // for high-score leads, and it handles platform differences on the Rust side.
      await invoke('notify_high_score_lead', { title, body });
    } catch {
      /* notifications are a nicety, never a failure path */
    }
  },

  async relaunch(): Promise<void> {
    const { relaunch } = await import('@tauri-apps/plugin-process');
    await relaunch();
  },

  async checkUpdate(): Promise<UpdateInfo> {
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const update = await check();
      if (!update) return { available: false };
      return {
        available: true,
        version: update.version,
        install: async onProgress => {
          let downloaded = 0;
          let total: number | null = null;
          await update.downloadAndInstall(event => {
            if (event.event === 'Started') {
              total = event.data.contentLength ?? null;
            } else if (event.event === 'Progress') {
              downloaded += event.data.chunkLength;
              onProgress?.(downloaded, total);
            }
          });
          const { relaunch } = await import('@tauri-apps/plugin-process');
          await relaunch();
        },
      };
    } catch {
      return { available: false };
    }
  },

  watchBackend(onChange: (endpoint: BackendEndpoint | null) => void): () => void {
    let cancelled = false;
    let port: number | null = null;
    let token: string | null = null;
    let published = '';
    const unlisteners: Array<() => void> = [];
    let poll: number | undefined;

    const publish = () => {
      if (cancelled) return;
      if (port === null || !token) return;
      const key = `${port}:${token}`;
      // Republishing an unchanged endpoint would make consumers tear down a healthy WebSocket.
      if (key === published) return;
      published = key;
      onChange({ port, token });
    };

    const sync = async () => {
      if (cancelled) return;
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        try {
          token = await invoke<string>('get_api_token');
        } catch {
          /* not ready yet */
        }
        try {
          port = await invoke<number>('get_sidecar_port');
        } catch {
          /* not ready yet */
        }
        publish();
      } catch {
        /* IPC unavailable */
      }
    };

    void (async () => {
      await sync();
      // Poll as well as subscribe: an event can fire before the listener is attached during
      // startup, and polling is what recovers from that without a race.
      poll = window.setInterval(() => {
        if (!cancelled && (port === null || !token)) void sync();
      }, 1000);

      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisteners.push(
          await listen<number>('sidecar-port', ev => {
            port = ev.payload;
            publish();
          }),
        );
        unlisteners.push(
          await listen<string>('sidecar-token', ev => {
            token = ev.payload;
            publish();
          }),
        );
        unlisteners.push(
          await listen('sidecar-terminated', () => {
            // The endpoint is gone, not merely stale. Consumers must drop the connection rather
            // than retry against a dead port.
            port = null;
            token = null;
            published = '';
            onChange(null);
          }),
        );
      } catch {
        /* events unavailable; the poll above still resolves the endpoint */
      }
    })();

    return () => {
      cancelled = true;
      if (poll !== undefined) window.clearInterval(poll);
      for (const off of unlisteners) off();
    };
  },

  async getBackendError(): Promise<string | null> {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      return await invoke<string>('get_sidecar_error');
    } catch {
      return null;
    }
  },

  async pickDirectory(): Promise<string | null> {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      return await invoke<string | null>('pick_directory');
    } catch {
      return null;
    }
  },
};
