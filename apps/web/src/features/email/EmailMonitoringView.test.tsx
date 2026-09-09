import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "bun:test";
import type { ApiFetch } from "../../types";
import { EmailMonitoringView } from "./EmailMonitoringView";

const api: ApiFetch = async () => new Response(JSON.stringify({
  configured: false,
  enabled: false,
  processed_count: 0,
  mailbox: null,
}));

describe("EmailMonitoringView", () => {
  it("makes inbox monitoring and all setup actions visible", () => {
    const html = renderToStaticMarkup(<EmailMonitoringView api={api} />);

    expect(html).toContain("Let your inbox update your applications.");
    expect(html).toContain("Application email updates");
    expect(html).toContain("Connect inbox");
    expect(html).toContain("Test connection");
    expect(html).toContain("Check now");
  });
});
