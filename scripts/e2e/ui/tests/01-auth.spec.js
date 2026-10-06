// Anmeldung: Login/Logout, Sprachwechsel (Sprachdatei erst bei Auswahl), 401 -> Neuladen der Login-Seite,
// Rollen-Gate. Quellen: W0-INT-FE1a (Smoke-Checkliste), F8b Nr. 2, FE1b.
const { test, expect, EMPTY_STATE } = require('../fixtures/test')
const { PanelApi } = require('../fixtures/api')
const { t, tl, exact } = require('../fixtures/i18n')
const { loginViaUi, expectLoggedIn, navLink } = require('../fixtures/ui')

test.describe('Anmeldung', () => {
  test.use({ storageState: EMPTY_STATE })

  let user
  test.beforeAll(async ({ adminApi }) => {
    user = await adminApi.createUser({ role: 'user' })
  })
  test.afterAll(async ({ adminApi }) => {
    if (user) await adminApi.deleteUser(user.id)
  })

  test('Login: falsches Passwort -> Fehler, richtiges -> Dashboard, Logout -> Login-Seite', async ({ page }) => {
    await loginViaUi(page, user.username, `${user.password}-falsch`)
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/login$/)

    await page.locator('form input[type="password"]').fill(user.password)
    await page.getByRole('button', { name: exact(t('login.submit')) }).click()
    await expectLoggedIn(page)
    await expect(page).toHaveURL(/\/$/)
    // Nicht-Admin: "Meine Zonen" statt "Alle Zonen", keine Benutzerverwaltung
    await expect(navLink(page, 'layout.myZones')).toBeVisible()
    await expect(navLink(page, 'layout.users')).toHaveCount(0)

    await page.getByRole('button', { name: t('layout.logout') }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('button', { name: exact(t('login.submit')) })).toBeVisible()
    // Sitzung ist weg: geschuetzte Seite fuehrt zurueck zur Anmeldung
    await page.goto('/zones')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('Sprachwechsel auf der Login-Seite laedt die Sprachdatei erst bei Auswahl und bleibt gemerkt', async ({ page }) => {
    const huChunks = []
    page.on('request', (req) => {
      if (/\/assets\/hu-[^/]*\.js$/.test(new URL(req.url()).pathname)) huChunks.push(req.url())
    })
    await page.goto('/login')
    await expect(page.getByRole('button', { name: exact(t('login.submit')) })).toBeVisible()
    expect(huChunks, 'ungarische Sprachdatei vor der Auswahl').toEqual([])

    await page.getByRole('button', { name: t('settings.language') }).click()
    await page.getByRole('option', { name: /Magyar/ }).click()
    await expect(page.getByRole('button', { name: exact(tl('hu', 'login.submit')) })).toBeVisible()
    expect(huChunks.length, 'ungarische Sprachdatei nach der Auswahl geladen').toBeGreaterThan(0)

    await page.reload()
    await expect(page.getByRole('button', { name: exact(tl('hu', 'login.submit')) })).toBeVisible()

    await page.getByRole('button', { name: tl('hu', 'settings.language') }).click()
    await page.getByRole('option', { name: /Deutsch/ }).click()
    await expect(page.getByRole('button', { name: exact(t('login.submit')) })).toBeVisible()
  })

  test('401 waehrend der Sitzung: Neuladen auf /login', async ({ page, context }) => {
    await loginViaUi(page, user.username, user.password)
    await expectLoggedIn(page)
    await navLink(page, 'layout.myZones').click()
    await expect(page).toHaveURL(/\/zones$/)
    // alle Anfragen der Zonenseite abgeschlossen, bevor die Sitzung verschwindet
    await page.waitForLoadState('networkidle')

    // Sitzung "abgelaufen": Cookie weg, der naechste API-Aufruf liefert 401 -> api.js laedt /login neu
    await context.clearCookies()
    const fullLoad = page.waitForEvent('load')
    await navLink(page, 'layout.overview').click()
    await fullLoad
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('button', { name: exact(t('login.submit')) })).toBeVisible()
  })

  test('Rollen-Gate: Nicht-Admin sieht auf /users und /audit nur "Kein Zugriff"', async ({ openAs }) => {
    const api = await PanelApi.login(user.username, user.password)
    try {
      const page = await openAs(api)
      const usersCalls = []
      page.on('request', (req) => { if (req.url().includes('/api/v1/auth/users')) usersCalls.push(req.url()) })
      for (const path of ['/users', '/audit']) {
        await page.goto(path)
        await expect(page.getByRole('heading', { name: t('common.noAccessTitle') })).toBeVisible()
        await expect(page.getByText(t('common.noAccessAdminOnly'))).toBeVisible()
      }
      expect(usersCalls, 'keine Anfrage an /auth/users als Nicht-Admin').toEqual([])
    } finally {
      await api.dispose()
    }
  })
})
