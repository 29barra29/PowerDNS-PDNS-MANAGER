// Dialoge (A11y-Smoke, W1-NACHARBEIT 5.6 und a11y-PENDING-Liste): Jeder Dialog liegt sichtbar ueber der Seite (Abdeckung
// ueber das ganze Fenster, alle Knoepfe klickbar), der Fokus liegt im Dialog und bleibt dort (Tab/Umschalt+Tab), ESC
// schliesst und der Fokus kehrt zum ausloesenden Knopf zurueck (alle Dialoge nutzen seit WS-W2-NACHARBEIT useDialogFocus).
const { test, expect } = require('../fixtures/test')
const { PanelApi, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath, dialog, modal, rowWith, expectFocusInside, expectFocusTrapped, expectDialogOnTop } = require('../fixtures/ui')

/**
 * Standardpruefung eines Dialogs. opts: escCloses (Default true).
 * Seit WS-W3-NACHARBEIT haengen alle Dialog-Overlays per ModalPortal an document.body (UI-SMOKE-1 behoben) und der
 * Fokus kehrt auch zu einem Ausloeser zurueck, der waehrend des Dialogs kurz deaktiviert war (UI-SMOKE-3 behoben).
 */
async function checkDialog(page, opener, dlg, { escCloses = true } = {}) {
  await opener.click()
  await expect(dlg).toBeVisible()
  await expectDialogOnTop(dlg)
  const focus = async () => {
    await expectFocusInside(dlg)
    await expectFocusTrapped(page, dlg, 6)
  }
  await focus()
  if (!escCloses) return
  await page.keyboard.press('Escape')
  await expect(dlg).toBeHidden()
  await expect(opener).toBeFocused()
}

function settingsCard(page, headingKey) {
  return page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t(headingKey) }) })
}

test.describe('Dialoge: Lage, Fokus, ESC', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Webhook-Formular (Einstellungen)', async ({ page }) => {
    await page.goto('/settings?tab=integrations')
    const card = settingsCard(page, 'settings.integrations.webhooks')
    await checkDialog(page, card.getByRole('button', { name: t('webhooks.add') }).first(), dialog(page, t('webhooks.createTitle')))
  })

  test('Panel-Token-Formular (Einstellungen)', async ({ page }) => {
    await page.goto('/settings?tab=integrations')
    const card = settingsCard(page, 'settings.integrations.panelTokens')
    await checkDialog(page, card.getByRole('button', { name: t('panelTokens.create') }).first(), dialog(page, t('panelTokens.modalCreateTitle')))
  })

  test('DynDNS-Token-Formular (Einstellungen)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-dyn')
    zones.push(zone)
    await adminApi.createZone(zone) // ohne schreibbare Zone ist "Token erstellen" gesperrt
    await page.goto('/settings?tab=integrations')
    const card = settingsCard(page, 'dyndns.title')
    await checkDialog(page, card.getByRole('button', { name: t('dyndns.createToken') }), dialog(page, t('dyndns.createToken')))
  })

  test('Rollback-Vorschau (Zonenverlauf)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-rb')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: `www.${zone}`, type: 'A', contents: ['192.0.2.140'] })
    await page.goto(`${zonePath(zone)}?tab=history`)
    const entry = page.locator('li').filter({ hasText: 'www A' }).first()
    await checkDialog(page, entry.getByRole('button', { name: t('history.rollback') }), dialog(page, pattern('history.rollbackTitle')))
  })

  test('Aufbewahrung (Audit-Log)', async ({ page }) => {
    await page.goto('/audit')
    await checkDialog(page, page.getByRole('button', { name: t('audit.retentionButton') }), dialog(page, t('audit.retentionTitle')))
  })

  test('TTL fuer die Auswahl (Bulk)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-ttl')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: `www.${zone}`, type: 'A', contents: ['192.0.2.141'] })
    await page.goto(zonePath(zone))
    await rowWith(page, '192.0.2.141').getByRole('checkbox', { name: t('bulk.selectRow') }).check()
    const toolbar = page.getByRole('toolbar', { name: t('bulk.toolbarLabel') })
    await checkDialog(page, toolbar.getByRole('button', { name: t('bulk.actionSetTtl') }), dialog(page, t('bulk.ttlDialogTitle')))
  })

  test('DNSSEC aktivieren (Zonenansicht)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-dnssec')
    zones.push(zone)
    await adminApi.createZone(zone)
    await page.goto(zonePath(zone))
    const card = page.getByRole('region', { name: t('dnssec.cardTitle') })
    await checkDialog(page, card.getByRole('button', { name: t('dnssec.btnEnable') }), dialog(page, pattern('dnssec.enableTitle')))
  })

  test('Passwort & Sicherheit (Benutzer)', async ({ page, adminApi }) => {
    const user = await adminApi.createUser()
    try {
      await page.goto('/users')
      const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: user.username, exact: true }) })
      await checkDialog(page, card.getByRole('button', { name: t('users.securityTitle') }), page.getByRole('dialog', { name: t('users.securityTitle') }))
    } finally {
      await adminApi.deleteUser(user.id)
    }
  })

  test('Step-up (SSO-Reiter)', async ({ page }) => {
    await page.goto('/settings?tab=sso')
    const card = settingsCard(page, 'settings.sso.oidcTitle')
    await card.getByLabel(t('settings.sso.issuer')).fill('https://idp-dialog.example.com/')
    const save = card.getByRole('button', { name: exact(t('common.save')) })
    // Der Speichern-Knopf ist waehrend des Step-ups deaktiviert; der Hook gibt den Fokus zurueck, sobald er wieder
    // aktiv ist (UI-SMOKE-3)
    await checkDialog(page, save, dialog(page, t('stepUp.title')))
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('Einmal-Anzeige (Scrape-Token, Monitoring): ESC schliesst nicht', async ({ page, adminApi }) => {
    const before = await adminApi.get('settings/metrics')
    if (before.token_set) await adminApi.del('settings/metrics/token')
    try {
      await page.goto('/settings?tab=monitoring')
      const opener = page.getByRole('button', { name: t('settings.monitoring.metricsTokenCreate') })
      const once = dialog(page, t('settings.monitoring.metricsTokenModalTitle'))
      await checkDialog(page, opener, once, { escCloses: false })
      await page.keyboard.press('Escape')
      await expect(once).toBeVisible()
      await once.getByRole('button', { name: t('settings.monitoring.metricsTokenDone') }).click()
      await expect(once).toBeHidden()
    } finally {
      await adminApi.del('settings/metrics/token').catch(() => {})
    }
  })

  test('Neue Zone (Zonenliste, seit WS-W3-NACHARBEIT mit role="dialog")', async ({ page }) => {
    await page.goto('/zones')
    const opener = page.getByRole('main').getByRole('button', { name: t('zones.newZone') }).first()
    await checkDialog(page, opener, page.getByRole('dialog', { name: t('zones.createZone') }))
  })

  test('Mobile Seitenleiste: Dialog mit Fokus-Falle, ESC und Fokus-Rueckgabe', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    const opener = page.getByRole('button', { name: t('layout.openMenu') })
    const nav = page.getByRole('dialog', { name: t('layout.menu') })
    await expect(nav).toHaveCount(0)   // geschlossen: kein Dialog
    await opener.click()
    await expect(nav).toBeVisible()
    await expectFocusInside(nav)
    await expectFocusTrapped(page, nav, 6)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: t('layout.menu') })).toHaveCount(0)
    await expect(opener).toBeFocused()
  })

  // Aeltere Dialoge ohne role="dialog" (Karte .glass-card mit Ueberschrift): nur die Lage wird geprueft
  test('Aeltere Dialoge (Server, Vorlagen, ACME, Zone, Benutzer, Record) liegen ueber der Seite', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-old')
    zones.push(zone)
    await adminApi.createZone(zone)
    const cases = [
      ['/settings?tab=servers', 'settingsMore.addServer', 'settingsMore.addNewServer'],
      ['/settings?tab=templates', 'templates.newTemplate', 'templates.createNewTemplate'],
      ['/settings?tab=acme', 'settings.acme.newToken', 'settings.acme.newToken'],
      ['/zones', 'zones.newZone', 'zones.createZone'],
      ['/users', 'users.newUser', 'users.createUser'],
      [zonePath(zone), 'zoneDetail.addRecord', 'zoneDetail.addRecord'],
    ]
    const problems = []
    for (const [path, openerKey, titleKey] of cases) {
      await page.goto(path)
      await page.getByRole('main').getByRole('button', { name: t(openerKey) }).first().click()
      const box = modal(page, t(titleKey))
      await expect(box).toBeVisible()
      try {
        await expectDialogOnTop(box)
      } catch (err) {
        problems.push(`${path}: ${String(err.message).split('\n').slice(0, 6).join(' ')}`)
      }
    }
    expect(problems, 'Dialoge, die nicht ueber der Seite liegen').toEqual([])
  })
})
