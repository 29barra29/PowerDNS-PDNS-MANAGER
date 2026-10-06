// Benutzerverwaltung (F2/F3, F14): Benutzer anlegen mit Passwortzwang, Zwangsdialog beim ersten Login;
// Dialog "Passwort & Sicherheit": Zugaenge-Zaehler, API-Tokens des Benutzers ("Alle widerrufen"), 2FA-Reset,
// Zufallspasswort (Einmal-Anzeige, ESC schliesst nicht, Kopieren), "Alle Zugaenge widerrufen"; danach Login ohne
// 2FA mit Zwangswechsel; externes SSO-Konto (Badge mit Aussteller/Rollen-Hinweis, Abschnitt "Externe Anmeldung",
// Umwandeln mit Step-up, Loeschen mit JIT-Hinweis). Quellen: WS-F2F3 (Spec 9.3), WS-F14-APP 5 (Admin-Sicht),
// W1-NACHARBEIT 5.6, WS-F10-APP-FE 7 + fix2 (Antrag an WS-UI-SMOKE).
const { test, expect } = require('../fixtures/test')
const { PanelApi, receiver, unique, strongPassword } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { field, modal, dialog, acceptNextConfirm, loginViaUi, expectLoggedIn, expectFocusInside } = require('../fixtures/ui')
const { pendingCheck } = require('../fixtures/pending')
const { db } = require('../fixtures/db')
const { ADMIN_PASSWORD } = require('../fixtures/env')

// L12/a11y-PENDING: UserSecurityModal bekommt useDialogFocus erst durch WS-W2-NACHARBEIT (Welle 3, Punkt 5)
const FOCUS_OWNER = 'WS-W2-NACHARBEIT (UserSecurityModal mit useDialogFocus)'

/** Zwangsdialog "Passwort aendern" ausfuellen (aktuelles -> neues Passwort). */
async function completeForcedChange(page, current, next) {
  await expect(page.getByRole('heading', { name: t('forcePassword.title') })).toBeVisible()
  const inputs = page.locator('form input[type="password"]')
  await inputs.nth(0).fill(current)
  await inputs.nth(1).fill(next)
  await inputs.nth(2).fill(next)
  await page.getByRole('button', { name: exact(t('forcePassword.submit')) }).click()
  await expectLoggedIn(page)
}

/** Karte eines Benutzers in der Liste (per Anzeigename). */
function userCard(page, name) {
  return page.locator('.glass-card').filter({ has: page.getByRole('heading', { name, exact: true }) })
}

test.describe('Benutzerverwaltung', () => {
  const cleanup = []
  test.afterAll(async ({ adminApi }) => {
    for (const fn of cleanup) await fn(adminApi)
  })

  test('Benutzer anlegen mit Passwortzwang -> Zwangsdialog beim ersten Login', async ({ page, openAs }) => {
    const username = unique('uinew')
    const password = strongPassword()
    cleanup.push(async (api) => { const u = await api.findUser(username); if (u) await api.deleteUser(u.id) })

    await page.goto('/users')
    await page.getByRole('button', { name: t('users.newUser') }).click()
    const create = modal(page, t('users.createUser'))
    await field(create, t('users.username')).fill(username)
    await field(create, t('users.displayName')).fill(`UI ${username}`)
    await field(create, t('users.email')).fill(`${username}@example.com`)
    await field(create, t('users.password')).fill(password)
    await create.locator('label').filter({ hasText: t('users.mustChangeOnFirstLogin') }).locator('input').check()
    await create.getByRole('button', { name: exact(t('settings.create')) }).click()
    await expect(page.getByText(t('users.userCreated', { name: username }))).toBeVisible()
    await expect(userCard(page, `UI ${username}`)).toBeVisible()

    // Erster Login des neuen Benutzers in eigenem Browser-Kontext
    const userPage = await openAs(null)
    await loginViaUi(userPage, username, password)
    await completeForcedChange(userPage, password, strongPassword())
  })

  test('Sicherheitsdialog: Tokens, 2FA-Reset, Zufallspasswort, Zugaenge widerrufen', async ({ page, adminApi, openAs }) => {
    const seeded = await adminApi.createUser()
    cleanup.push((api) => api.deleteUser(seeded.id))
    // Laufende Browser-Sitzung des Benutzers (zweiter Kontext) fuer die Pruefung "Widerruf beendet Sitzungen" (L3);
    // vor dem Einschalten von 2FA angemeldet (ein TOTP-Code darf nur einmal benutzt werden)
    const sessionApi = await PanelApi.login(seeded.username, seeded.password)
    const userSession = await openAs(sessionApi)
    await sessionApi.dispose()
    // Benutzer mit 2FA, zwei API-Tokens und einem Webhook
    const userApi = await PanelApi.login(seeded.username, seeded.password)
    await userApi.enableTotp()
    for (const n of ['ui-a', 'ui-b']) await userApi.post('auth/me/panel-tokens', { name: n })
    await userApi.post('auth/me/webhooks', { name: unique('ui-hook'), url: receiver.url(unique('ui-users')), events: ['*'] })
    await userApi.dispose()
    await userSession.goto('/zones')
    await expect(userSession).toHaveURL(/\/zones$/)

    await page.goto('/users')
    const card = userCard(page, seeded.username)
    await expect(card.getByLabel(`${t('users.apiTokenCountTitle')}: 2`)).toBeVisible()
    const openBtn = card.getByRole('button', { name: t('users.securityTitle') })
    await openBtn.click()
    const sec = page.getByRole('dialog', { name: t('users.securityTitle') })
    await expect(sec.getByText(t('users.securityFor', { name: seeded.username }))).toBeVisible()
    await pendingCheck(FOCUS_OWNER, () => expectFocusInside(sec))

    // API-Tokens dieses Benutzers: Liste + "Alle widerrufen"
    await expect(sec.getByRole('button', { name: `${t('panelTokens.revoke')}: ui-a` })).toBeVisible()
    let confirm = acceptNextConfirm(page)
    await sec.getByRole('button', { name: t('users.apiTokensRevokeAll') }).click()
    expect(await confirm).toContain(seeded.username)
    await expect(sec.getByText(t('users.apiTokensRevoked', { count: 2 }))).toBeVisible()

    // 2FA zuruecksetzen
    confirm = acceptNextConfirm(page)
    await sec.getByRole('button', { name: t('users.reset2fa') }).click()
    await confirm
    await expect(sec.getByText(t('users.reset2faDone', { name: seeded.username }))).toBeVisible()

    // Zugaenge widerrufen (Webhook wird deaktiviert)
    confirm = acceptNextConfirm(page)
    await sec.getByRole('button', { name: t('users.revokeAccessButton') }).click()
    await confirm
    await expect(sec.getByText(t('users.revokeAccessDone', { tokens: 0, dyndns: 0, webhooks: 1, deliveries: 0 }))).toBeVisible()
    // L3: Der Widerruf beendet auch laufende Browser-Sitzungen (WS-W2-NACHARBEIT, users.sessions_revoked_at)
    await pendingCheck('WS-W2-NACHARBEIT (L3: Widerruf beendet Browser-Sitzungen)', async () => {
      await userSession.goto('/search')
      await expect(userSession).toHaveURL(/\/login$/)
    })

    // Zufallspasswort mit Zwangswechsel: Einmal-Anzeige, ESC schliesst nicht, Kopieren
    await sec.locator('label').filter({ hasText: t('users.mustChangeOnNextLogin') }).nth(1).locator('input').check()
    confirm = acceptNextConfirm(page)
    await sec.getByRole('button', { name: t('users.generateButton') }).click()
    await confirm
    const once = dialog(page, t('users.oneTimePasswordTitle', { name: seeded.username }))
    await expect(once).toBeVisible()
    const randomPw = (await once.getByLabel(t('secretModal.secretLabel')).innerText()).trim()
    expect(randomPw.length).toBeGreaterThanOrEqual(16)
    await page.keyboard.press('Escape')
    await expect(once).toBeVisible()
    await once.getByRole('button', { name: t('secretModal.copy') }).click()
    await expect(once.getByText(t('secretModal.copied'))).toBeVisible()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(randomPw)
    // "Gesichert" schliesst die Einmal-Anzeige und den Sicherheitsdialog
    await once.getByRole('button', { name: t('users.oneTimeDone') }).click()
    await expect(once).toBeHidden()
    await expect(sec).toBeHidden()
    await pendingCheck(FOCUS_OWNER, () => expect(openBtn).toBeFocused())

    // Login mit dem Zufallspasswort ohne 2FA -> Zwangswechsel
    const userPage = await openAs(null)
    await loginViaUi(userPage, seeded.username, randomPw)
    await completeForcedChange(userPage, randomPw, strongPassword())
    const tokens = await adminApi.get(`auth/users/${seeded.id}/panel-tokens`)
    const list = Array.isArray(tokens) ? tokens : tokens?.tokens || []
    expect(list.filter((tok) => !tok.revoked_at && tok.status !== 'revoked')).toEqual([])
  })

  test('Externes SSO-Konto: Badge, Externe Anmeldung, Umwandeln (Step-up), Loeschen mit JIT-Hinweis', async ({ page, adminApi }) => {
    const issuer = 'https://idp.example.com/realms/e2e'
    const conv = await adminApi.createUser()
    const del = await adminApi.createUser()
    cleanup.push((api) => api.deleteUser(conv.id), (api) => api.deleteUser(del.id))
    // Zustand "per SSO angelegt" laesst sich ohne IdP nur in der Datenbank herstellen
    for (const u of [conv, del]) {
      await db('UPDATE users SET auth_source = ?, external_issuer = ?, external_id = ? WHERE id = ?',
        ['oidc', issuer, `ext-${u.username}`, u.id])
    }
    const sso = await adminApi.get('settings/sso')
    const restore = { role_mode: sso.oidc.role_mode, admin_groups: sso.oidc.admin_groups }
    await adminApi.put('settings/sso', { oidc: { role_mode: 'sync', admin_groups: ['pdns-admins'] }, step_up: { current_password: ADMIN_PASSWORD } })
    try {
      await page.goto('/users')
      const card = userCard(page, conv.username)
      const badge = card.getByText(t('users.authSourceOidc'), { exact: true })
      await expect(badge).toBeVisible()
      await expect(badge).toHaveAttribute('title', `${t('users.authSourceTitle', { issuer })} – ${t('users.roleManagedBySso')}`)

      // Sicherheitsdialog eines externen Kontos: kein Zufallspasswort, Abschnitt "Externe Anmeldung"
      await card.getByRole('button', { name: t('users.securityTitle') }).click()
      const sec = page.getByRole('dialog', { name: t('users.securityTitle') })
      await expect(sec.getByText(t('users.externalManagedHint'))).toBeVisible()
      await expect(sec.getByText(issuer)).toBeVisible()
      await expect(sec.getByText(`ext-${conv.username}`)).toBeVisible()
      await expect(sec.getByRole('button', { name: t('users.generateButton') })).toHaveCount(0)

      // Umwandeln: Rueckfrage -> Step-up (lokaler Admin) -> Einmalpasswort
      const convertBtn = sec.getByRole('button', { name: t('users.convertToLocal') })
      const stepUp = dialog(page, t('stepUp.title'))
      // Step-up ueber einem Dialog: ESC schliesst nur den Step-up, der Sicherheitsdialog bleibt
      const first = acceptNextConfirm(page)
      await convertBtn.click()
      await first
      await expect(stepUp).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(stepUp).toBeHidden()
      await expect(sec).toBeVisible()
      const confirm = acceptNextConfirm(page)
      await convertBtn.click()
      expect(await confirm).toContain(t('users.convertToLocalHint'))
      await stepUp.getByLabel(t('settings.currentPassword')).fill(ADMIN_PASSWORD)
      await stepUp.getByRole('button', { name: exact(t('stepUp.confirm')) }).click()
      const once = dialog(page, t('users.oneTimePasswordTitle', { name: conv.username }))
      await expect(once).toBeVisible()
      await once.getByRole('button', { name: t('users.oneTimeDone') }).click()
      await expect(once).toBeHidden()
      await expect(page.getByText(t('users.convertedToLocal', { name: conv.username }))).toBeVisible()
      await expect(userCard(page, conv.username).getByText(t('users.authSourceOidc'), { exact: true })).toHaveCount(0)
      const row = (await db('SELECT auth_source, external_id FROM users WHERE id = ?', [conv.id]))[0]
      expect(row).toEqual({ auth_source: 'local', external_id: null })

      // Loeschen eines externen Kontos: Rueckfrage mit JIT-Hinweis
      const question = acceptNextConfirm(page)
      await userCard(page, del.username).getByRole('button', { name: t('users.delete') }).click()
      expect(await question).toBe(t('users.deleteExternalConfirm', { name: del.username }))
      await expect(page.getByText(t('users.userDeleted', { name: del.username }))).toBeVisible()
    } finally {
      await adminApi.put('settings/sso', { oidc: restore, step_up: { current_password: ADMIN_PASSWORD } }).catch(() => {})
    }
  })
})
