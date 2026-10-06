// Propagation-Tab (F12F13-FE): Deep-Link ?tab=propagation, Pruefung mit Record-Vergleich gegen beide PowerDNS-Server
// (Serial, Status, Record-Abgleich), Zonenwechsel ohne alte Ergebnisse. Quelle: WS-F12F13-FE 6 (Spec 9.3).
const { test, expect } = require('../fixtures/test')
const { uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath } = require('../fixtures/ui')

test.describe('Propagation', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Deep-Link, Pruefung mit Record-Vergleich, Zonenwechsel ohne alte Ergebnisse', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-prop')
    const other = uniqueZone('ui-prop2')
    zones.push(zone, other)
    await adminApi.createZone(zone)
    await adminApi.createZone(other)
    await adminApi.addRecord('ns1', zone, { name: `www.${zone}`, type: 'A', contents: ['192.0.2.120'] })

    await page.goto(`${zonePath(zone)}?tab=propagation`)
    await expect(page.getByRole('tab', { name: t('zoneDetail.tabPropagation') })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('heading', { name: t('propagation.title') })).toBeVisible()

    // Beim Oeffnen laeuft die Pruefung automatisch (SOA-Serial aller Panel-Server)
    await expect(page.getByText(pattern('propagation.expectedSerial'))).toBeVisible({ timeout: 45_000 })
    // Externe Abfragen sind im E2E-Stand aus: Hinweis mit Link zu den Einstellungen (nur Admin)
    await expect(page.getByText(t('propagation.externalDisabledAdmin'))).toBeVisible()

    await page.getByLabel(t('propagation.recordLabel')).fill('www')
    await page.getByRole('combobox', { name: t('propagation.colRecord') }).selectOption('A')
    await page.getByRole('button', { name: exact(t('propagation.rerunCheck')) }).click()
    const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: t('propagation.colSerial') }) })
    await expect(table.locator('tbody tr').first()).toBeVisible()
    await expect(table.getByRole('columnheader', { name: t('propagation.colRecord'), exact: true })).toBeVisible()
    await expect(table.locator('tbody')).toContainText('192.0.2.120')
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByRole('button', { name: exact(t('propagation.rerunCheck')) })).toBeVisible()

    // Andere Zone: eigene Pruefung, nichts von der vorherigen Zone (kein Record-Vergleich, leeres Feld)
    await page.goto(`${zonePath(other)}?tab=propagation`)
    await expect(page.getByRole('heading', { name: t('propagation.title') })).toBeVisible()
    await expect(page.getByLabel(t('propagation.recordLabel'))).toHaveValue('')
    await expect(page.getByText(pattern('propagation.expectedSerial'))).toBeVisible({ timeout: 45_000 })
    await expect(page.getByRole('columnheader', { name: t('propagation.colRecord'), exact: true })).toHaveCount(0)
    await expect(page.locator('table tbody')).not.toContainText('192.0.2.120')
  })
})
