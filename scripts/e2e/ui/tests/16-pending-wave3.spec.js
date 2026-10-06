// Welle-3-UI, die beim Schreiben dieser Specs noch nicht integriert war (Basis: integrierte Welle 2):
//   WS-F15   LUA-Record anlegen (als Admin) mit Warnbox, Vorlage und Server-Status
//   WS-F5-FE Secrets-Status-Karte im Reiter "Sicherheit"
//   WS-F4-C  DS in der Elternzone (Deaktivieren-Dialog) ohne externe Abfragen
// Standard: SKIP mit TODO. Nach dem Merge der Workstreams mit `scripts/e2e/ui/run.sh --pending` (bzw.
// E2E_UI_PENDING=1) ausfuehren; Selektoren folgen den Specs (F15 §2.2/§6.5, F5 §6.2, F4 §2.9) und den i18n-Keys dort.
// Gruen -> skipUnlessPending-Zeile entfernen (dann laufen sie immer mit).
const { test, expect } = require('../fixtures/test')
const { uniqueZone } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { zonePath, modal, field, dialog, rowWith } = require('../fixtures/ui')
const { skipUnlessPending } = require('../fixtures/pending')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Welle 3 (nach Integration)', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('WS-F15: LUA-Record als Admin mit Vorlage, Warnbox und Server-Status', async ({ page, adminApi }) => {
    skipUnlessPending('WS-F15 (LUA-UI: LuaTemplatePicker, LuaWarningBox, LuaValue)')
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
    skipUnlessPending('WS-F5-FE (SecretsStatusCard im Sicherheits-Reiter)')
    await page.goto('/settings?tab=security')
    await expect(page.getByRole('heading', { name: t('settings.secrets.title') })).toBeVisible()
    // Frischer E2E-Stand: Schluessel angelegt, alle Geheimnisse verschluesselt und lesbar
    await expect(page.getByText(t('settings.secrets.statusOk'))).toBeVisible()
    await expect(page.getByText(t('settings.secrets.statusPlaintext'))).toHaveCount(0)
  })

  test('WS-F4-C: DS in der Elternzone beim Deaktivieren (externe Abfragen aus)', async ({ page, adminApi }) => {
    skipUnlessPending('WS-F4-C (Parent-DS-/DNSKEY-Pruefung in DnssecDisableModal/DnssecRolloverModal)')
    const zone = uniqueZone('ui-pds')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.post(`dnssec/ns1/${encodeURIComponent(zone)}/enable`, {})

    await page.goto(zonePath(zone))
    const card = page.getByRole('region', { name: t('dnssec.cardTitle') })
    await card.getByRole('button', { name: t('dnssec.btnDisable') }).click()
    const dlg = dialog(page, t('dnssec.disableTitle', { zone: bare(zone) }))
    await expect(dlg.getByText(t('dnssec.parentDsTitle'))).toBeVisible()
    // Propagation (externe Resolver) ist im E2E-Stand aus -> Hinweis statt Pruefung (F4 §2.9 Teil B)
    await expect(dlg.getByText(t('dnssec.parentDsDisabled', { zone: bare(zone) }), { exact: false })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dlg).toBeHidden()
  })
})
