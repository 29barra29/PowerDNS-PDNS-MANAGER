// Welle-3-UI (seit dem Wellenende 3 integriert; urspruenglich als Pending-Specs gegen Welle 2 geschrieben):
//   WS-F15   LUA-Record anlegen (als Admin) mit Warnbox, Vorlage und Server-Status
//   WS-F5-FE Secrets-Status-Karte im Reiter "Sicherheit"; unlesbares SMTP-Passwort und 2FA-Geheimnis (per DB)
//   WS-F4-C  Rollover-Schritt "DNSKEY pruefen" und DS in der Elternzone (Deaktivieren), ohne und mit DNS-Pruefung
// Selektoren folgen den Specs (F15 §2.2/§6.5, F5 §6.2, F4 §2.9) und den i18n-Keys dort.
const { test, expect } = require('../fixtures/test')
const dns = require('node:dns').promises
const { PanelApi, uniqueZone, pdns } = require('../fixtures/api')
const { db } = require('../fixtures/db')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath, modal, field, dialog, rowWith } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Welle 3 (nach Integration)', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('WS-F15: LUA-Record als Admin mit Vorlage, Warnbox und Server-Status', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-lua')
    zones.push(zone)
    await adminApi.createZone(zone)

    await page.goto(zonePath(zone))
    await page.getByRole('button', { name: t('zoneDetail.addRecord') }).first().click()
    const add = modal(page, t('zoneDetail.addRecord'))
    await field(add, t('zoneDetail.recordType')).selectOption({ label: t('zoneDetail.recordLUA') })
    await field(add, t('zoneDetail.nameRelative')).fill('lua')

    // Warnbox (amber, role=note) mit Server-Status je relevantem Server (pdns.conf: enable-lua-records=yes)
    const warn = add.getByRole('note').filter({ hasText: t('lua.warnTitle') })
    await expect(warn).toBeVisible()
    await expect(warn).toContainText(t('lua.statusTitle'))
    await expect(warn).toContainText('ns1')

    // Vorlage "Gewichtete Verteilung" fuellt Ziel-Typ und Code
    await add.getByRole('combobox').filter({ hasText: t('lua.templatesChoose') }).first()
      .selectOption({ label: t('lua.templates.pickwrandom.label') })
    await expect(field(add, t('lua.fieldCode'))).toHaveValue(/pickwrandom/)
    await add.getByRole('button', { name: exact(t('common.save')) }).click()
    await expect(page.getByText(t('zoneDetail.recordCreated', { type: 'LUA', name: `lua.${bare(zone)}` }))).toBeVisible()
    await expect(rowWith(page, 'pickwrandom')).toBeVisible()
    await expect.poll(async () => (await adminApi.rrsetContents('ns2', zone, `lua.${zone}`, 'LUA')).join(' ')).toContain('pickwrandom')
  })

  test('WS-F5-FE: Secrets-Status im Reiter "Sicherheit"', async ({ page }) => {
    await page.goto('/settings?tab=security')
    await expect(page.getByRole('heading', { name: t('settings.secrets.title') })).toBeVisible()
    // Frischer E2E-Stand: Schluessel angelegt, alle Geheimnisse verschluesselt und lesbar
    await expect(page.getByText(t('settings.secrets.statusOk'))).toBeVisible()
    await expect(page.getByText(t('settings.secrets.statusPlaintext'))).toHaveCount(0)
  })

  test('WS-F5-FE: unlesbare Geheimnisse (SMTP-Passwort, 2FA eines Benutzers) in Status-Karte und SMTP-Reiter', async ({ page, adminApi }) => {
    const user = await adminApi.createUser()
    try {
      const userApi = await PanelApi.login(user.username, user.password)
      await userApi.enableTotp()
      await userApi.dispose()
      await adminApi.put('settings/smtp', { host: 'smtp.ui-smoke.test', port: 587, username: 'u', password: 'ui-smtp', from_email: 'noreply@example.com', encryption: 'starttls', enabled: false })
      // Chiffretexte, die sich nicht entschluesseln lassen (wie nach einem Schluesselverlust)
      await db('UPDATE users SET totp_secret = ? WHERE id = ?', ['enc:v1:dWktc21va2U6a2FwdXR0', user.id])
      await db("UPDATE system_settings SET value = ? WHERE `key` = 'smtp_password'", ['enc:v1:dWktc21va2U6a2FwdXR0'])

      await page.goto('/settings?tab=security')
      await expect(page.getByText(pattern('settings.secrets.statusUnreadable'))).toBeVisible()
      await expect(page.getByText(t('settings.secrets.unreadableTotp', { username: user.username }))).toBeVisible()
      await expect(page.getByText(t('settings.secrets.unreadableSetting', { field: t('settings.secrets.fieldSmtpPassword') }))).toBeVisible()
      await page.getByRole('link', { name: t('settings.secrets.goToUsers') }).first().click()
      await expect(page).toHaveURL(/\/users/)

      await page.goto('/settings?tab=smtp')
      await expect(page.getByText(t('settings.smtpPasswordUnreadable'))).toBeVisible()
    } finally {
      await adminApi.put('settings/smtp', { host: '', port: 587, username: '', password: '', from_email: '', encryption: 'starttls', enabled: false }).catch(() => {})
      await adminApi.deleteUser(user.id)
    }
  })

  test('WS-F4-C: Rollover-Schritt "DNSKEY pruefen" und Elternzone beim Deaktivieren (externe Abfragen aus)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-pds')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.post(`dnssec/ns1/${encodeURIComponent(zone)}/enable`, {})

    await page.goto(zonePath(zone))
    const card = page.getByRole('region', { name: t('dnssec.cardTitle') })

    // Rollover starten: DNSKEY-Pruefung ist ohne Propagations-Freigabe "nicht geprueft" -> Pflicht-Checkbox
    await card.getByRole('button', { name: t('dnssec.btnRollover') }).click()
    const roll = dialog(page, t('dnssec.rolloverTitle', { zone: bare(zone) }))
    await roll.getByRole('button', { name: t('dnssec.rolloverStartButton') }).click()
    await expect(roll.getByText(pattern('dnssec.dnskeyCheckDisabled'))).toBeVisible()
    const switchBtn = roll.getByRole('button', { name: t('dnssec.rolloverSwitchButton') })
    await expect(switchBtn).toBeDisabled()
    await roll.locator('label').filter({ hasText: t('dnssec.dnskeyCheckConfirm') }).locator('input[type="checkbox"]').check()
    await roll.locator('label').filter({ hasText: t('dnssec.rolloverConfirmDsAdded') }).locator('input[type="checkbox"]').check()
    await expect(switchBtn).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(roll).toBeHidden()

    // Deaktivieren: Abschnitt "DS in der Elternzone" mit manuellem Weg (Pruefung nicht freigegeben)
    await card.getByRole('button', { name: t('dnssec.btnDisable') }).click()
    const dlg = dialog(page, t('dnssec.disableTitle', { zone: bare(zone) }))
    await expect(dlg.getByText(t('dnssec.parentDsTitle'), { exact: true })).toBeVisible()
    await expect(dlg.getByText(t('dnssec.parentDsDisabled', { zone: bare(zone) }))).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dlg).toBeHidden()
  })

  test('WS-F4-C: mit DNS-Pruefung (Resolver = pdns1, Kindzone mit Glue): DNSKEY-Pruefung und Elternzone', async ({ page, adminApi }) => {
    const { address: ip1 } = await dns.lookup('pdns1', { family: 4 })
    const parent = uniqueZone('ui-f4c')
    const child = `sub.${parent}`
    const nsChild = `ns1.${child}`
    zones.push(child, parent)
    const before = await adminApi.get('settings/propagation')
    try {
      // Aufbau wie scripts/e2e/checks/f4_c.py: Zonen nur auf ns1, Delegation mit Glue auf pdns1
      await adminApi.put('settings/propagation', { enabled: true, check_authoritative: true, resolvers: [ip1] })
      await adminApi.createZone(child, { nameservers: [nsChild], servers: ['ns1'] })
      await adminApi.addRecord('ns1', child, { name: nsChild, type: 'A', contents: [ip1] })
      await adminApi.createZone(parent, { nameservers: ['ns1.e2e.test.'], servers: ['ns1'] })
      await pdns.replace('ns1', parent, child, 'NS', [nsChild])
      await adminApi.post(`dnssec/ns1/${encodeURIComponent(child)}/enable`, {})

      await page.goto(zonePath(child))
      const card = page.getByRole('region', { name: t('dnssec.cardTitle') })
      await card.getByRole('button', { name: t('dnssec.btnRollover') }).click()
      const roll = dialog(page, t('dnssec.rolloverTitle', { zone: bare(child) }))
      await roll.getByRole('button', { name: t('dnssec.rolloverStartButton') }).click()
      await expect(roll.getByText(pattern('dnssec.dnskeyCheckIntro'))).toBeVisible()
      await pdns.flush('ns1', child)
      await roll.getByRole('button', { name: t('dnssec.dnskeyCheckButton') }).first().click()
      await expect(roll.getByText(pattern('dnssec.dnskeyCheckAllOk'))).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(roll).toBeHidden()

      await card.getByRole('button', { name: t('dnssec.btnDisable') }).click()
      const dlg = dialog(page, t('dnssec.disableTitle', { zone: bare(child) }))
      await dlg.getByRole('button', { name: t('dnssec.parentDsCheck') }).click()
      await expect(dlg.getByText(pattern('dnssec.parentDsRowNone')).first()).toBeVisible()
      await expect(dlg.getByText(t('dnssec.parentDsNoneVisible'))).toBeVisible()
      await page.keyboard.press('Escape')
    } finally {
      const keys = ['enabled', 'check_authoritative', 'ipv6', 'resolvers']
      await adminApi.put('settings/propagation', Object.fromEntries(keys.map((k) => [k, before[k]]))).catch(() => {})
    }
  })
})
