import { afterEach, beforeEach, describe, expect, it } from "bun:test";

import { httpBaseFor, isTauri, wsBaseFor } from "./index";

/**
 * The platform layer is what makes one codebase serve both shells (ARCHITECTURE.md D3), so the
 * things tested here are the ones that fail *silently* when wrong: shell detection, and address
 * resolution that quietly becomes cross-origin.
 */

const originalWindow = globalThis.window;

/* Bun's mock toolkit has no stubGlobal; a direct assignment is equivalent here. */
function setWindow(value: unknown) {
  (globalThis as any).window = value;
}

function setLocation(protocol: string, host: string) {
  setWindow({ location: { protocol, host } });
}

afterEach(() => {
  if (originalWindow === undefined) {
    // @ts-expect-error restoring a deleted global in a node environment
    delete globalThis.window;
  } else {
    setWindow(originalWindow);
  }
});

describe("shell detection", () => {
  it("detects Tauri v2 via __TAURI_INTERNALS__", () => {
    setWindow({ __TAURI_INTERNALS__: {} });
    expect(isTauri()).toBe(true);
  });

  it("still detects Tauri v1 via __TAURI__", () => {
    // Both are checked so an upgrade can't silently flip the app into web mode inside the desktop
    // shell — which would look like a working app that addresses the wrong backend.
    setWindow({ __TAURI__: {} });
    expect(isTauri()).toBe(true);
  });

  it("reports web for a plain browser", () => {
    setLocation("http:", "localhost:8000");
    expect(isTauri()).toBe(false);
  });

  it("does not throw without a window", () => {
    setWindow(undefined);
    expect(isTauri()).toBe(false);
  });
});

describe("address resolution", () => {
  describe("in a browser", () => {
    beforeEach(() => setLocation("http:", "localhost:8000"));

    it("stays same-origin instead of forcing loopback", () => {
      // The regression this guards: hardcoding 127.0.0.1 makes every call cross-origin when the
      // user typed "localhost", leaving the app dependent on CORS to function at all.
      expect(httpBaseFor(8000)).toBe("http://localhost:8000");
      expect(wsBaseFor(8000)).toBe("ws://localhost:8000");
    });

    it("ignores the passed port in favour of the serving origin", () => {
      // The API serves the page, so its port IS the page's port. A mismatched argument must not
      // send requests somewhere the page can't reach.
      expect(httpBaseFor(9999)).toBe("http://localhost:8000");
    });

    it("upgrades the websocket scheme under https", () => {
      setLocation("https:", "app.example.com");
      expect(wsBaseFor(443)).toBe("wss://app.example.com");
      expect(httpBaseFor(443)).toBe("https://app.example.com");
    });
  });

  describe("in the desktop shell", () => {
    beforeEach(() => setWindow({ __TAURI_INTERNALS__: {}, location: { protocol: "tauri:", host: "localhost" } }));

    it("addresses the sidecar explicitly on loopback", () => {
      // The desktop UI is served from tauri://localhost, so its own origin is useless for reaching
      // the Python sidecar — it must be named.
      expect(httpBaseFor(51234)).toBe("http://127.0.0.1:51234");
      expect(wsBaseFor(51234)).toBe("ws://127.0.0.1:51234");
    });
  });

  describe("without a DOM", () => {
    it("falls back to loopback rather than throwing", () => {
      // Unit tests and non-browser consumers reach this; reading window.location unguarded threw
      // and surfaced as an unrelated "cannot read properties of undefined" in API tests.
      setWindow(undefined);
      expect(httpBaseFor(8000)).toBe("http://127.0.0.1:8000");
      expect(wsBaseFor(8000)).toBe("ws://127.0.0.1:8000");
    });

    it("falls back when window exists but has no host", () => {
      setWindow({ location: { protocol: "http:", host: "" } });
      expect(httpBaseFor(8000)).toBe("http://127.0.0.1:8000");
    });
  });
});
