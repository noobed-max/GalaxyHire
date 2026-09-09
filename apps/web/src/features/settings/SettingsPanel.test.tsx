import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { GlobalSettings } from "./panels/GlobalSettings";
import { EMPTY } from "./panels/shared";
import type { ApiFetch } from "../../types";

const modal = readFileSync(new URL("./SettingsModal.tsx", import.meta.url), "utf8");
const globalPanel = readFileSync(new URL("./panels/GlobalSettings.tsx", import.meta.url), "utf8");
const sharedPanel = readFileSync(new URL("./panels/shared.tsx", import.meta.url), "utf8");
const topnav = readFileSync(new URL("../../shared/components/TopNav.tsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../../App.tsx", import.meta.url), "utf8");
const onboarding = readFileSync(new URL("../../shared/components/OnboardingWizard.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../../index.css", import.meta.url), "utf8");

describe("Settings UI contracts", () => {
  const api: ApiFetch = async () => new Response(null, { status: 200 });

  it("surfaces backend validation errors", () => {
    expect(modal).toContain("saveError");
    expect(modal).toContain("Settings could not be saved");
  });

  it("submits settings through the API client", () => {
    expect(modal).toContain("/api/v1/settings");
    expect(modal).toContain("method: \"POST\"");
  });

  it("keeps LLM provider fields in the global panel", () => {
    expect(globalPanel).toContain("llm_provider");
    expect(globalPanel).toContain("openai");
  });

  it("treats OpenCode as a separate provider with its own complete configuration", () => {
    const pills = sharedPanel.slice(sharedPanel.indexOf("export const PROVIDERS"), sharedPanel.indexOf("SUBSCRIPTION_PROVIDERS"));
    expect(pills).toContain('id: "custom",    label: "OpenAI-compatible"');
    expect(pills).toContain('id: "anthropic", label: "Anthropic"');
    expect(pills).toContain('id: "opencode",  label: "OpenCode"');

    for (const field of [
      "opencode_api_key",
      "opencode_model",
      "opencode_base_url",
      "opencode_protocol",
      "opencode_endpoint_type",
      "opencode_reasoning_effort",
    ]) {
      expect(sharedPanel).toContain(field);
    }
    expect(sharedPanel).toContain('opencode_base_url: "https://opencode.ai/zen/go/v1"');
    expect(sharedPanel).toContain('opencode_endpoint_type: "responses"');
    expect(sharedPanel).toContain('opencode_reasoning_effort: "medium"');
    expect(globalPanel).toContain('opencode: {');
    expect(globalPanel).toContain('settingsApi.deleteKey(api, fields.apiKey)');
  });

  it("renders OpenCode defaults in its own provider panel", () => {
    const markup = renderToStaticMarkup(
      <GlobalSettings
        cfg={{ ...EMPTY, llm_provider: "opencode" }}
        set={() => () => {}}
        onChange={() => {}}
        prov="opencode"
        api={api}
      />,
    );
    expect(markup).toContain("OpenCode endpoint URL");
    expect(markup).toContain('value="https://opencode.ai/zen/go/v1"');
    expect(markup).toContain('value="muse-spark-1.3-contributor"');
    expect(markup).toContain("Responses");
    expect(markup).toContain("Medium");

    const compatibleMarkup = renderToStaticMarkup(
      <GlobalSettings
        cfg={{ ...EMPTY, llm_provider: "custom" }}
        set={() => () => {}}
        onChange={() => {}}
        prov="custom"
        api={api}
      />,
    );
    expect(compatibleMarkup).toContain("Compatible endpoint URL");
    expect(compatibleMarkup).not.toContain("OpenCode endpoint URL");
  });

  it("serializes OpenCode defaults during onboarding", () => {
    expect(onboarding).toContain('<option value="opencode">OpenCode</option>');
    expect(onboarding).toContain('payload.opencode_base_url = "https://opencode.ai/zen/go/v1"');
    expect(onboarding).toContain('payload.opencode_endpoint_type = "responses"');
    expect(onboarding).toContain('payload.opencode_reasoning_effort = "medium"');
  });

  it("allows normal page text selection while keeping graph drag surfaces non-selectable", () => {
    const bodyRules = styles.match(/body\s*\{([\s\S]*?)\}/)?.[1] || "";
    expect(bodyRules).toContain("user-select: text");
    expect(bodyRules).not.toContain("user-select: none");
    expect(styles).toContain(".graph-atlas-stage,\n.graph-embedding-stage");
    expect(styles).toContain("-webkit-user-select: none;\n  user-select: none;");
  });

  it("validates provider keys against the current form values", () => {
    expect(globalPanel).toContain("settingsApi.validate(api, cfg)");
  });

  it("model field is free text (no provider catalog fetch, no 400-model dropdowns)", () => {
    // The picker is a plain input: catalogs go stale and OpenRouter alone
    // lists 400+ ids. The user types the exact id their endpoint serves.
    expect(sharedPanel).toContain('placeholder="Model id — e.g. gpt-4o-mini, claude-sonnet-4-6, vendor/model-id"');
    expect(sharedPanel).not.toContain("settingsApi.models");
    expect(sharedPanel).not.toContain("useEffect(() => { reload(); }");
    // The stale "Load models" button is gone from the global panel.
    expect(globalPanel).not.toContain("Load models");
  });

  it("does not offer OpenRouter as a provider", () => {
    const pills = sharedPanel.slice(sharedPanel.indexOf("export const PROVIDERS"), sharedPanel.indexOf("SUBSCRIPTION_PROVIDERS"));
    expect(pills).not.toContain("openrouter");
    expect(sharedPanel.slice(sharedPanel.indexOf("export const KEY_FIELD"), sharedPanel.indexOf("const SECRET_MASK"))).not.toContain("openrouter");
    // Legal & Privacy section removed from the modal.
    expect(modal).not.toContain("LegalSettings");
    expect(modal).not.toContain("Terms of Use");
  });

  it("does not expose removed JustHireMe automation and discovery controls", () => {
    expect(modal).not.toContain("AutomationSettings");
    expect(modal).not.toContain("DiscoverySettings");
    expect(modal).not.toContain("StepSettings");
  });

  it("exposes email monitoring as a first-class screen as well as a settings panel", () => {
    expect(modal).toContain("EmailOutcomeSettings");
    expect(topnav).toContain('label: "Email updates"');
    expect(app).toContain('view === "email"');
    expect(app).toContain("<EmailMonitoringView");
  });
});
