// Dialoge (A11y-Smoke, W1-NACHARBEIT 5.6 und a11y-PENDING-Liste): Jeder Dialog liegt sichtbar ueber der Seite (Abdeckung
// ueber das ganze Fenster, alle Knoepfe klickbar), der Fokus liegt im Dialog und bleibt dort (Tab/Umschalt+Tab), ESC
// schliesst und der Fokus kehrt zum ausloesenden Knopf zurueck (alle Dialoge nutzen seit WS-W2-NACHARBEIT useDialogFocus).
const { test, expect } = require('../fixtures/test')
const { PanelApi, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath, dialog, modal, rowWith, expectFocusInside, expectFocusTrapped, expectDialogOnTop } = require('../fixtures/ui')
const { knownBug } = require('../fixtures/pending')

const BUG_CARD = 'UI-SMOKE-1: Dialoge in .glass-card-Karten (backdrop-filter) sind auf die Karte begrenzt'

/**
 * Standardpruefung eines Dialogs. opts: escCloses (Default true),
 * onTopBug (ID eines bekannten Fehlers "Dialog nicht ueber der Seite"), focusReturnBug (ID: Fokus kehrt nicht zurueck).
 */
async function checkDialog(page, opener, dlg, { escCloses = true, onTopBug = null, focusReturnBug = null } = {}) {
  await opener.click()
  await expect(dlg).toBeVisible()
  if (onTopBug) await knownBug(onTopBug, () => expectDialogOnTop(dlg))
  else await expectDialogOnTop(dlg)
  const focus = async () => {
    await expectFocusInside(dlg)
    await expectFocusTrapped(page, dlg, 6)
  }
  await focus()
  if (!escCloses) return
  await page.keyboard.press('Escape')
  await expect(dlg).toBeHidden()
  const back = focusReturnBug
    ? () => knownBug(focusReturnBug, () => expect(opener).toBeFocused({ timeout: 3_000 }))
    : () => expect(opener).toBeFocused()
  await back()
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
    await checkDialog(page, card.getByRole('button', { name: t('webhooks.add') }).first(), dialog(page, t('webhooks.createTitle')),
      { onTopBug: BUG_CARD })
  })

  test('Panel-Token-Formular (Einstellungen)', async ({ page }) => {
    await page.goto('/settings?tab=integrations')
    const card = settingsCard(page, 'settings.integrations.panelTokens')
    await checkDialog(page, card.getByRole('button', { name: t('panelTokens.create') }).first(), dialog(page, t('panelTokens.modalCreateTitle')),
      { onTopBug: BUG_CARD })
  })

  test('DynDNS-Token-Formular (Einstellungen)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dlg-dyn')
    zones.push(zone)
    await adminApi.createZone(zone) // ohne schreibbare Zone ist "Token erstellen" gesperrt
    await page.goto('/settings?tab=integrations')
    const card = settingsCard(page, 'dyndns.title')
    await checkDialog(page, card.getByRole('button', { name: t('dyndns.createToken') }), dialog(page, t('dyndns.createToken')),
      { onTopBug: BUG_CARD })
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
    // Der Speichern-Knopf ist waehrend des Step-ups deaktiviert -> der Hook kann den Fokus nicht zurueckgeben
    await checkDialog(page, save, dialog(page, t('stepUp.title')), {
      focusReturnBug: 'UI-SMOKE-3: Fokus nach Step-up-Abbruch nicht zurueck (Ausloeser waehrend des Wartens deaktiviert)',
    })
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('Einmal-Anzeige (Scrape-Token, Monitoring): ESC schliesst nicht', async ({ page, adminApi }) => {
    const before = await adminApi.get('settings/metrics')
    if (before.token_set) await adminApi.del('settings/metrics/token')
    try {
      await page.goto('/settings?tab=monitoring')
      const opener = page.getByRole('button', { name: t('settings.monitoring.metricsTokenCreate') })
      const once = dialog(page, t('settings.monitoring.metricsTokenModalTitle'))
      await checkDialog(page, opener, once, {
        escCloses: false,
        onTopBug: BUG_CARD,
      })
      await page.keyboard.press('Escape')
      await expect(once).toBeVisible()
      await once.getByRole('button', { name: t('settings.monitoring.metricsTokenDone') }).click()
      await expect(once).toBeHidden()
    } finally {
      await adminApi.del('settings/metrics/token').catch(() => {})
    }
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
