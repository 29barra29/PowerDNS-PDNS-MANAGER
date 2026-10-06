// Einstellungen: alle Reiter (Admin) in fester Reihenfolge, jeder laedt ohne API-Fehler; Deep-Link ?tab=,
// URL-Sync ohne History-Eintraege; Nicht-Admin-Reiter; SMTP-Passwort-Maske (behalten/entfernen);
// Monitoring (Status, Scrape-Token mit Einmal-Anzeige). Quellen: W0-INT-FE1b Review-Checkliste 1-3,
// F8b Nr. 5, WS-F12F13-FE 6. Secrets-Status (WS-F5-FE, Welle 3): 16-pending-wave3.spec.js.
const { test, expect } = require('../fixtures/test')
const { PanelApi } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { field, navLink, dialog, acceptNextConfirm } = require('../fixtures/ui')

// Reihenfolge laut Plan (Slot-order); "dns" erscheint, weil mindestens eine DNS-Karte (PTR) existiert
const ADMIN_TABS = [
  ['profile', 'settings.profile', 'settings.profileEdit'],
  ['integrations', 'settings.integrationsTab', 'settings.integrations.panelTokens'],
  ['servers', 'settings.servers', 'settingsMore.powerDnsServers'],
  ['dns', 'settings.dnsTab', 'ptr.settingsTitle'],
  ['templates', 'settings.templates', 'settings.templatesTitle'],
  ['smtp', 'settings.smtp', 'settings.smtpTitle'],
  ['welcome', 'settings.welcomeMail.tab', 'settings.welcomeMail.title'],
  ['security', 'settings.captcha.tab', 'settings.captcha.title'],
  ['sso', 'settings.sso.tab', 'settings.sso.title'],
  ['acme', 'settings.acme.tab', 'settings.acme.title'],
  ['monitoring', 'settings.monitoring.tab', 'settings.monitoring.statusTitle'],
  ['updates', 'settings.updates', 'settingsMore.updateTitle'],
  ['about', 'settings.about', 'settingsMore.aboutTitle'],
]

function tabButton(page, labelKey) {
  return page.getByRole('main').getByRole('button', { name: t(labelKey), exact: true })
}

/** Beschriftungen der Reiterleiste in DOM-Reihenfolge. */
async function tabLabels(page) {
  const bar = tabButton(page, 'settings.profile').locator('xpath=..')
  return (await bar.locator(':scope > button').allInnerTexts()).map((s) => s.trim())
}

test.describe('Einstellungen', () => {
  test('Admin: alle Reiter laden ohne Fehler, Reihenfolge, Deep-Link und URL-Sync', async ({ page }) => {
    const apiErrors = []
    page.on('response', (res) => {
      if (res.url().includes('/api/v1/') && res.status() >= 400) apiErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`)
    })
    await page.goto('/zones')
    await navLink(page, 'layout.settings').click()
    await expect(page.getByRole('heading', { name: t('settings.title') })).toBeVisible()
    expect(await tabLabels(page)).toEqual(ADMIN_TABS.map(([, label]) => t(label)))

    for (const [id, label, heading] of ADMIN_TABS) {
      await tabButton(page, label).click()
      await expect(page).toHaveURL(new RegExp(`[?&]tab=${id}(&|$)`))
      await expect(page.getByRole('heading', { name: t(heading) }).first()).toBeVisible()
    }
    // Reiter-Wechsel ersetzt den History-Eintrag: "Zurueck" fuehrt zur vorherigen Seite, nicht zum vorigen Reiter
    await page.goBack()
    await expect(page).toHaveURL(/\/zones$/)
    expect(apiErrors, 'API-Fehler beim Laden der Reiter').toEqual([])

    // Deep-Link und unbekannter Reiter
    await page.goto('/settings?tab=templates')
    await expect(page.getByRole('heading', { name: t('settings.templatesTitle') })).toBeVisible()
    await page.goto('/settings?tab=gibtsnicht')
    await expect(page.getByRole('heading', { name: t('settings.profileEdit') })).toBeVisible()
  })

  test('Nicht-Admin: nur Profil, API & Sicherheit, Ueber; Admin-Reiter per Deep-Link -> Profil', async ({ adminApi, openAs }) => {
    const user = await adminApi.createUser()
    const api = await PanelApi.login(user.username, user.password)
    try {
      const page = await openAs(api)
      await page.goto('/settings?tab=servers')
      await expect(page.getByRole('heading', { name: t('settings.profileEdit') })).toBeVisible()
      expect(await tabLabels(page)).toEqual([t('settings.profile'), t('settings.integrationsTab'), t('settings.about')])
      await expect(page.getByRole('heading', { name: t('settingsMore.powerDnsServers') })).toHaveCount(0)
    } finally {
      await api.dispose()
      await adminApi.deleteUser(user.id)
    }
  })

  test('SMTP: gespeichertes Passwort wird nie angezeigt, Leerfeld behaelt es, "entfernen" loescht es', async ({ page, adminApi }) => {
    await page.goto('/settings?tab=smtp')
    await field(page, t('settings.smtpServer')).fill('smtp.ui-smoke.test')
    await page.locator('input[type="password"]').first().fill('ui-smtp-geheim')
    await page.getByRole('button', { name: exact(t('common.save')) }).first().click()
    await expect(page.getByText(t('settings.smtpSaveSuccess'))).toBeVisible()
    expect((await adminApi.get('settings/smtp')).password_set).toBe(true)

    // Neu laden: Feld leer mit Platzhalter, Wert nie im DOM
    await page.reload()
    const pw = page.locator('input[type="password"]').first()
    await expect(field(page, t('settings.smtpServer'))).toHaveValue('smtp.ui-smoke.test')
    await expect(pw).toHaveValue('')
    await expect(pw).toHaveAttribute('placeholder', t('settings.smtpPasswordKeepPlaceholder'))
    expect(await page.content()).not.toContain('ui-smtp-geheim')

    // Speichern mit leerem Feld behaelt das Passwort
    await page.getByRole('button', { name: exact(t('common.save')) }).first().click()
    await expect(page.getByText(t('settings.smtpSaveSuccess'))).toBeVisible()
    expect((await adminApi.get('settings/smtp')).password_set).toBe(true)

    // "Passwort entfernen"
    await page.locator('label').filter({ hasText: t('settings.smtpPasswordClear') }).locator('input[type="checkbox"]').check()
    await expect(page.getByText(t('settings.smtpPasswordWillBeCleared'))).toBeVisible()
    await page.getByRole('button', { name: exact(t('common.save')) }).first().click()
    await expect(page.getByText(t('settings.smtpSaveSuccess'))).toBeVisible()
    expect((await adminApi.get('settings/smtp')).password_set).toBe(false)
  })

  test('Monitoring: Status, Propagation-Einstellungen, Scrape-Token mit Einmal-Anzeige', async ({ page, adminApi }) => {
    await page.goto('/settings?tab=monitoring')
    await expect(page.getByRole('heading', { name: t('settings.monitoring.statusTitle') })).toBeVisible()
    await expect(page.getByText(new RegExp(`${t('settings.monitoring.statusOverallOk')}|${t('settings.monitoring.statusOverallProblem')}`)).first()).toBeVisible()
    await expect(page.getByText(exact(t('settings.monitoring.statusTasksTitle'))).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: t('settings.monitoring.propTitle') })).toBeVisible()
    await expect(page.getByRole('heading', { name: t('settings.monitoring.metricsTitle') })).toBeVisible()

    const before = await adminApi.get('settings/metrics')
    if (before.token_set) await adminApi.del('settings/metrics/token')
    await page.reload()
    await page.getByRole('button', { name: t('settings.monitoring.metricsTokenCreate') }).click()
    const once = dialog(page, t('settings.monitoring.metricsTokenModalTitle'))
    await expect(once).toBeVisible()
    const secret = (await once.getByLabel(t('secretModal.secretLabel')).innerText()).trim()
    expect(secret.length).toBeGreaterThan(20)
    // Kopieren laesst das Token sichtbar; ESC schliesst die Einmal-Anzeige nicht
    await once.getByRole('button', { name: t('secretModal.copy') }).click()
    await expect(once.getByText(t('secretModal.copied'))).toBeVisible()
    await expect(once.getByLabel(t('secretModal.secretLabel'))).toHaveText(secret)
    await page.keyboard.press('Escape')
    await expect(once).toBeVisible()
    await once.getByRole('button', { name: t('settings.monitoring.metricsTokenDone') }).click()
    await expect(once).toBeHidden()
    expect((await adminApi.get('settings/metrics')).token_set).toBe(true)

    const confirm = acceptNextConfirm(page)
    await page.getByRole('button', { name: t('settings.monitoring.metricsTokenDelete') }).click()
    await confirm
    await expect(page.getByText(t('settings.monitoring.metricsTokenDeleted'))).toBeVisible()
    expect((await adminApi.get('settings/metrics')).token_set).toBe(false)
  })
})
