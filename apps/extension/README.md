# GalaxyHire — Extension (MV3)

The user-facing product: a Chrome side-panel workbench that talks to the [backend](../backend/).
Built with React 19 + Vite + Tailwind v4 + `@crxjs/vite-plugin`.

**Phase 5a:** the discovery workbench — search, fit-scored job cards, profile editor, and the
tailor/apply panel with resume download.
**Phase 5b:** the autofiller — deterministic ATS field mappers + Fast LLM fill + the
never-auto-submit guard + `DataTransfer` resume attach, driven from "Fill this page".

## Build & load

```bash
npm install
npm run build          # tsc --noEmit && vite build → dist/
npm test               # vitest + jsdom: fill engine / guard / mappers (12 tests)
# Chrome → chrome://extensions → Developer mode → Load unpacked → select dist/
npm run dev            # HMR dev build
```

## What it does

- **Settings** — backend URL + API key (requests host permission for that origin on save), and
  the LLM endpoint config (for 5b autofill / profile import). "Save & connect" runs `/health`.
- **Profile** — structured editor: identity, role tags, skills (with tags), projects (bullets +
  skills + role tags), and the free-form "anything else" block. Saves to `PUT /profile`.
- **Search** — role + must-have skills + exclude-titles + remote + max-seniority + sort
  (best-fit / latest-first). Renders fit-scored **job cards**: fit meter + band, matched (green) /
  missing (struck) skills, "seen ×N", proof-project count, freshness, comp, legitimacy, and a
  "Hide" that posts `/feedback`.
- **Apply** — `POST /tailor` shows the selected projects, the ATS breakdown (keyword / skills /
  in-context bars + missing keywords), AI-risk lint, and one-click **download** of the tailored
  resume (PDF / DOCX) and cover letter via signed asset refs. "Open career page" deep-links out;
  the human uploads and clicks Apply.

## Layout

```
src/
├─ background.ts          service worker: opens panel, richest-frame FILL_PAGE, LLM proxy
├─ content.ts            autofiller orchestrator (guard → mappers → LLM → attach)
├─ sidepanel/             App shell (tabs + connection pill) + entry
├─ features/              Settings · Profile · Search · JobCard · Apply
├─ fill/                  engine · mappers · fastfill · guard · fileupload (+ *.test.ts)
├─ llm/                   proxy (chat via background, stream:false, capability variants)
└─ shared/                api client · storage (host perms) · types · messages · fillProfile
```

## Autofill safety

- **Never auto-submits.** A capture-phase guard drops every untrusted (script) click on
  Submit/Apply/Next and every untrusted form submission; only real user clicks pass.
- **Never invents.** Deterministic mappers only copy profile facts; the LLM pass is told to use
  only the profile and omit what it can't answer.
- **You click Apply.** The extension fills and (optionally) attaches the résumé; the final submit
  is always yours.

## Status

Builds + typechecks clean, **12 fill-mechanics tests pass** (vitest + jsdom). Not yet loaded in a
real browser (same bar as the autoapply reference). Agent-mode (page-agent) for conditional fields
is a documented follow-on; the deterministic + Fast-LLM path is complete.
