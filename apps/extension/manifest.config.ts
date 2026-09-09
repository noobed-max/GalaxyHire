import { defineManifest } from '@crxjs/vite-plugin'

export default defineManifest({
  manifest_version: 3,
  name: 'GalaxyHire',
  version: '0.1.0',
  description:
    'Search jobs matched to your profile, tailor an ATS-safe resume, and (soon) autofill the application.',

  // The backend is a fixed local sidecar (src/shared/backend.ts), so its origin is a static
  // host permission — no settings screen, no runtime grant. activeTab/scripting/tabs drive the
  // autofiller; sidePanel is the workbench.
  permissions: ['sidePanel', 'activeTab', 'scripting', 'tabs'],
  host_permissions: ['http://127.0.0.1:8080/*', 'http://localhost:8080/*'],

  // Career-site origins are still requested at runtime on first "Fill This Page" (docs/06 §1) —
  // avoids a broad static <all_urls> at review time.
  optional_host_permissions: ['http://*/*', 'https://*/*'],

  background: {
    service_worker: 'src/background.ts',
    type: 'module',
  },

  side_panel: {
    default_path: 'src/sidepanel/index.html',
  },

  action: {
    default_title: 'Open GalaxyHire',
    default_icon: 'galaxyhire-logo.png',
  },

  icons: {
    16: 'galaxyhire-logo.png',
    32: 'galaxyhire-logo.png',
    48: 'galaxyhire-logo.png',
    128: 'galaxyhire-logo.png',
  },

  // The autofiller. Declared for all frames (Greenhouse/Lever forms live in iframes); the
  // background worker picks the single richest frame to actually fill (docs/06 §3). Declaring
  // a content script only permits injection — host access is still granted at runtime.
  content_scripts: [
    {
      matches: ['<all_urls>'],
      js: ['src/content.ts'],
      all_frames: true,
      run_at: 'document_idle',
    },
  ],
})
