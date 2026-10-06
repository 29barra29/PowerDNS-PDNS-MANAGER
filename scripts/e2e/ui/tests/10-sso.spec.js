// SSO (F10-APP-FE) ohne echten IdP: SSO-Reiter mit Step-up-Dialog fuer ein lokales Admin-Konto (Abbrechen ohne
// Speichern, falsches Passwort -> Fehler im Dialog, richtiges -> gespeichert); Fehlerbanner der Login-Seite fuer alle
// sso_error-Codes (+ unbekannt -> generic, idp_error mit sso_detail), Parameter verschwinden aus der Adresszeile.
// Quellen: WS-F10-APP-FE Abschnitt 7 (Login-Seite, SSO-Tab), 6.3. Keycloak/Authentik/AD: nicht im E2E-Stack.
const { test, expect, EMPTY_STATE } = require('../fixtures/test')
const { ADMIN_PASSWORD } = require('../fixtures/env')
const { PanelApi } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { dialog, expectFocusInside } = require('../fixtures/ui')

// Codes laut components/sso/ssoModel.js (SSO_ERROR_KEYS) -> i18n-Key unter login.ssoError
const SSO_ERRORS = {
  disabled: 'disabled', config: 'config', discovery: 'discovery', state: 'state', token: 'token', id_token: 'idToken',
  no_account: 'noAccount', not_allowed: 'notAllowed', account_disabled: 'accountDisabled', link_conflict: 'linkConflict',
  link_failed: 'linkFailed', internal: 'internal', totp_unreadable: 'totp_unreadable', rate_limited: 'rateLimited',
}

test.describe('SSO', () => {
  test('SSO-Reiter: Issuer aendern verlangt Step-up (lokales Konto)', async ({ adminApi, openAs }) => {
    const before = await adminApi.get('settings/sso')
    const oldIssuer = before?.oidc?.issuer || ''
    const newIssuer = `https://idp-${Date.now()}.example.com/realms/e2e`
    // Eigenes Admin-Konto: der absichtliche Fehlversuch zaehlt in der Login-Drossel (5 je Benutzer/15 min)
    // nicht gegen den E2E-Admin, mit dem alle anderen Specs arbeiten
    const admin2 = await adminApi.createUser({ role: 'admin' })
    const api = await PanelApi.login(admin2.username, admin2.password)
    try {
      const page = await openAs(api)
      await page.goto('/settings?tab=sso')
      const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('settings.sso.oidcTitle') }) })
      await expect(card.getByText(t('settings.sso.stepUpHint'))).toBeVisible()
      await card.getByLabel(t('settings.sso.issuer')).fill(newIssuer)

      // Abbrechen: nichts gespeichert, kein Fehlerbanner
      const save = card.getByRole('button', { name: exact(t('common.save')) })
      await save.click()
      const stepUp = dialog(page, t('stepUp.title'))
      await expect(stepUp).toBeVisible()
      await expect(stepUp.getByText(t('stepUp.body'))).toBeVisible()
      await expectFocusInside(stepUp)
      await stepUp.getByRole('button', { name: exact(t('common.cancel')) }).click()
      await expect(stepUp).toBeHidden()
      await expect(page.getByRole('alert')).toHaveCount(0)
      expect((await adminApi.get('settings/sso')).oidc.issuer).toBe(oldIssuer)

      // Falsches Passwort: Fehler im Dialog, Dialog bleibt
      await save.click()
      await stepUp.getByLabel(t('settings.currentPassword')).fill(`${admin2.password}-falsch`)
      await stepUp.getByRole('button', { name: exact(t('stepUp.confirm')) }).click()
      await expect(stepUp.getByRole('alert')).toBeVisible()
      await expect(stepUp).toBeVisible()

      // Richtiges Passwort: gespeichert
      await stepUp.getByLabel(t('settings.currentPassword')).fill(admin2.password)
      await stepUp.getByRole('button', { name: exact(t('stepUp.confirm')) }).click()
      await expect(stepUp).toBeHidden()
      await expect(page.getByRole('status').filter({ hasText: t('settings.sso.saved') })).toBeVisible()
      expect((await adminApi.get('settings/sso')).oidc.issuer).toBe(newIssuer)
    } finally {
      await api.dispose()
      await adminApi.put('settings/sso', { oidc: { issuer: oldIssuer }, step_up: { current_password: ADMIN_PASSWORD } })
        .catch(() => {})
      await adminApi.deleteUser(admin2.id)
    }
  })

  test.describe('Login-Seite ohne Sitzung', () => {
    test.use({ storageState: EMPTY_STATE })

    test('Ohne SSO-Konfiguration: Formular wie 2.4.1, kein SSO-Knopf', async ({ page }) => {
      await page.goto('/login')
      await expect(page.getByRole('button', { name: exact(t('login.submit')) })).toBeVisible()
      await expect(page.getByRole('button', { name: /^Anmelden mit / })).toHaveCount(0)
      await expect(page.getByText(t('login.orLocal'))).toHaveCount(0)
    })

    test('Fehlerbanner je sso_error-Code; Parameter verschwinden aus der Adresszeile', async ({ page }) => {
      for (const [code, key] of Object.entries(SSO_ERRORS)) {
        await page.goto(`/login?sso_error=${code}`)
        await expect(page.getByRole('alert')).toHaveText(t(`login.ssoError.${key}`, { code: '–' }))
        await expect(page).toHaveURL(/\/login$/)
      }
      await page.goto('/login?sso_error=voellig_unbekannt')
      await expect(page.getByRole('alert')).toHaveText(t('login.ssoError.generic'))
      await page.goto('/login?sso_error=idp_error&sso_detail=access_denied')
      await expect(page.getByRole('alert')).toHaveText(t('login.ssoError.idpError', { code: 'access_denied' }))
      await expect(page).toHaveURL(/\/login$/)
    })
  })
})
