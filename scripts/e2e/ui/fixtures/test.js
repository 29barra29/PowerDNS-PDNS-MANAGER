// Gemeinsame Test-Basis: Konsolen-Waechter fuer jede Seite, Admin-API, weitere Browser-Sitzungen.
//
//   const { test, expect } = require('../fixtures/test')
//   test('...', async ({ page, guard, adminApi, openAs }) => { ... })
//
// - guard:    ConsoleGuard (automatisch aktiv); am Testende muss guard.problems() leer sein.
//             Erwartete Meldungen: guard.allow(/Text/).
// - adminApi: eingeloggte Admin-Sitzung ueber die API (Seed-Daten, Pruefungen, Aufraeumen).
// - openAs:   openAs(panelApi) -> neue Seite mit der Sitzung dieses Benutzers (eigener Browser-Kontext);
//             openAs(null) -> neue Seite ohne Anmeldung. Waechter und Routen (external.js) gelten auch dort.
// - page:     Standardseite, eingeloggt als Admin (storageState aus global-setup).
const base = require('@playwright/test')
const { ConsoleGuard } = require('./console-guard')
const { PanelApi } = require('./api')
const { routeExternal } = require('./external')

const EMPTY_STATE = { cookies: [], origins: [] }

const test = base.test.extend({
  guard: [
    async ({ blockedRequests }, use, testInfo) => {
      const guard = new ConsoleGuard()
      await use(guard)
      if (guard.entries.length) {
        await testInfo.attach('console.json', {
          body: JSON.stringify(guard.entries, null, 1),
          contentType: 'application/json',
        })
      }
      const problems = guard.problems()
      // Nur pruefen, wenn der Test selbst bestanden hat (sonst verdeckt dieser Fehler die eigentliche Ursache)
      if (testInfo.status === testInfo.expectedStatus) {
        base.expect(problems.map((p) => `[${p.kind}] ${p.text} (Seite ${p.page})`),
          'Konsolenfehler auf den besuchten Seiten').toEqual([])
        base.expect(blockedRequests, 'Anfragen an Ziele ausserhalb des E2E-Netzes').toEqual([])
      }
    },
    { auto: true },
  ],

  // Externe Anfragen: GitHub beantwortet, alles andere ausserhalb des E2E-Netzes blockiert (siehe external.js)
  blockedRequests: async ({}, use) => {
    await use([])
  },

  context: async ({ context, guard, blockedRequests }, use) => {
    guard.watchContext(context)
    await routeExternal(context, blockedRequests)
    await use(context)
  },

  adminApi: [
    async ({}, use) => {
      const api = await PanelApi.admin()
      await use(api)
      await api.dispose()
    },
    { scope: 'worker' },
  ],

  openAs: async ({ browser, guard, blockedRequests }, use) => {
    const contexts = []
    await use(async (panelApi, options = {}) => {
      const ctx = await browser.newContext({ storageState: panelApi ? await panelApi.storageState() : EMPTY_STATE, ...options })
      guard.watchContext(ctx)
      await routeExternal(ctx, blockedRequests)
      contexts.push(ctx)
      return ctx.newPage()
    })
    for (const ctx of contexts) await ctx.close().catch(() => {})
  },
})

module.exports = { test, expect: base.expect, EMPTY_STATE }
