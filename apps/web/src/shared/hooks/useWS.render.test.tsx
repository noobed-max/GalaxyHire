import { describe, expect, it, mock } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

/* Bun's runner has no `vi.mock` hoisting: register module mocks before the
 * hook (and its Tauri imports) load. */
mock.module("@tauri-apps/api/event", () => ({
  listen: () => Promise.resolve(() => undefined),
}));
mock.module("@tauri-apps/api/core", () => ({
  invoke: () => Promise.resolve(null),
}));

const { useWS } = await import("./useWS");

function HookProbe() {
  const { conn, port, apiToken, sidecarError, logs, progress } = useWS();
  return (
    <output>
      {conn}:{String(port)}:{String(apiToken)}:{String(sidecarError)}:{logs.length}:{String(progress.active)}
    </output>
  );
}

describe("useWS render defaults", () => {
  it("provides a disconnected snapshot before desktop events arrive", () => {
    const html = renderToStaticMarkup(<HookProbe />);
    expect(html).toContain("disconnected:null:null:null:0:false");
  });
});
