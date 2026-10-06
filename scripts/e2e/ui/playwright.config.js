// Playwright-Konfiguration der UI-Smoke-Tests (scripts/e2e/ui/README.md).
// Laeuft im Container (compose.ui.yaml, Dienst "ui") gegen das E2E-Backend http://backend:8000,
// das auch das gebaute Frontend ausliefert. Ein Worker (2-CPU-Host), Retries 1, keine festen Wartezeiten.
const path = require('node:path')
const { defineConfig, devices } = require('@playwright/test')

const OUT = process.env.E2E_UI_OUT || path.join(__dirname, 'out')
const BASE_URL = (process.env.E2E_BASE_URL || 'http://backend:8000').replace(/\/+$/, '')
const SETUP_URL = (process.env.E2E_SETUP_URL || '').replace(/\/+$/, '')
// Admin-Sitzung aus fixtures/global-setup.js; Tests ohne Anmeldung setzen test.use({ storageState: EMPTY_STATE })
const ADMIN_STATE = path.join(OUT, 'state', 'admin.json')

// http://backend:8000 ist kein "sicherer Kontext" (nur localhost/https waeren es). Ohne diese Freigabe
// fehlen navigator.clipboard und crypto.subtle; Kopier-Buttons liefen dann in den Fehlerpfad.
const secureOrigins = [BASE_URL, SETUP_URL].filter(Boolean).join(',')

module.exports = defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: Number.parseInt(process.env.E2E_UI_RETRIES ?? '1', 10) || 0,
  forbidOnly: !!process.env.CI,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: path.join(OUT, 'test-results'),
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(OUT, 'report'), open: 'never' }],
    ['json', { outputFile: path.join(OUT, 'results.json') }],
  ],
  globalSetup: require.resolve('./fixtures/global-setup.js'),
  use: {
    ...devices['Desktop Chrome'],
    baseURL: BASE_URL,
    storageState: ADMIN_STATE,
    locale: 'de-DE',
    timezoneId: 'Europe/Berlin',
    viewport: { width: 1366, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    permissions: ['clipboard-read', 'clipboard-write'],
    launchOptions: {
      args: secureOrigins ? [`--unsafely-treat-insecure-origin-as-secure=${secureOrigins}`] : [],
    },
  },
  // channel 'chromium' = volles Chromium im neuen Headless-Modus: nur dort wirkt
  // --unsafely-treat-insecure-origin-as-secure (die Headless-Shell ignoriert den Schalter).
  projects: [{ name: 'chromium', use: { channel: 'chromium' } }],
})
