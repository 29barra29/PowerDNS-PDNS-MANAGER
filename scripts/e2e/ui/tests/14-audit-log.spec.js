// Audit-Log (F7-FE): Filter mit URL-Sync, Paginierung, Detail-Drawer (Tastatur, ESC, Fokus-Rueckgabe), CSV-Export mit
// Filter, Aufbewahrungs-Dialog (Fokus auf dem Zahlfeld, ESC). Quellen: WS-F7-FE A4, W1-NACHARBEIT 5.6.
const fs = require('node:fs')
const { test, expect } = require('../fixtures/test')
const { uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { dialog, expectFocusInside } = require('../fixtures/ui')

// L11/a11y-PENDING: Detail-Drawer bekommt useDialogFocus erst durch WS-W2-NACHARBEIT (Welle 3, Punkt 5)

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Audit-Log', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Filter, Paginierung, Detail, CSV-Export und Aufbewahrung', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-audit')
    zones.push(zone)
    await adminApi.createZone(zone)
    for (const n of ['a', 'b', 'c']) {
      await adminApi.addRecord('ns1', zone, { name: `${n}.${zone}`, type: 'A', contents: [`192.0.2.${100 + n.charCodeAt(0) - 97}`] })
    }

    await page.goto('/audit')
    await expect(page.getByRole('heading', { name: t('audit.title') })).toBeVisible()

    // Paginierung ueber den Gesamtbestand (Checks und Specs erzeugen weit mehr als 25 Eintraege)
    const pager = page.getByRole('navigation', { name: pattern('common.pageRange') })
    await expect(pager).toContainText(/^1–50 /)
    await pager.getByRole('combobox').selectOption('25')
    await expect(pager).toContainText(/^1–25 /)
    await pager.getByRole('button', { name: t('common.next') }).click()
    await expect(pager).toContainText(/^26–50 /)
    await pager.getByRole('button', { name: t('common.prev') }).click()
    await expect(pager).toContainText(/^1–25 /)

    // Filter: Zone + Aktion, URL wird mitgefuehrt
    await page.getByLabel(t('audit.zone'), { exact: true }).fill(bare(zone))
    await expect(page).toHaveURL(/[?&]zone=/)
    await page.getByLabel(t('audit.action'), { exact: true }).selectOption('CREATE')
    await expect(page).toHaveURL(/[?&]action=CREATE/)
    const rows = page.locator('tbody tr')
    await expect(rows.filter({ hasText: `a.${bare(zone)}` })).toHaveCount(1)
    await expect(rows.filter({ hasText: `c.${bare(zone)}` })).toHaveCount(1)
    await expect(rows.filter({ hasText: 'ui-' }).filter({ hasNotText: bare(zone) })).toHaveCount(0)
    // Neuladen behaelt die Filter (URL)
    await page.reload()
    await expect(page.getByLabel(t('audit.zone'), { exact: true })).toHaveValue(bare(zone))
    await expect(rows.filter({ hasText: `b.${bare(zone)}` })).toHaveCount(1)

    // Detail-Drawer per Tastatur, ESC schliesst, Fokus zurueck auf der Zeile
    const row = rows.filter({ hasText: `b.${bare(zone)}` })
    await row.focus()
    await page.keyboard.press('Enter')
    const drawer = dialog(page, pattern('audit.detailTitle'))
    await expect(drawer).toBeVisible()
    await expectFocusInside(drawer)
    await expect(drawer).toContainText(`b.${bare(zone)}`)
    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    await expect(row).toBeFocused()

    // CSV-Export mit den aktiven Filtern
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: t('audit.exportCsv') }).click()
    const csv = fs.readFileSync(await (await download).path(), 'utf8')
    const lines = csv.trim().split(/\r?\n/)
    expect(lines.length).toBeGreaterThanOrEqual(4) // Kopfzeile + mindestens 3 Records
    for (const line of lines.slice(1)) expect(line).toContain(bare(zone))

    // Aufbewahrung: Fokus auf dem Zahlfeld nach dem Laden, ESC schliesst, Fokus zurueck
    const retentionBtn = page.getByRole('button', { name: t('audit.retentionButton') })
    await retentionBtn.click()
    const retention = dialog(page, t('audit.retentionTitle'))
    await expect(retention.getByLabel(t('audit.retentionDays'))).toBeFocused()
    await expect(retention.getByText(pattern('audit.retentionCurrent'))).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(retention).toBeHidden()
    await expect(retentionBtn).toBeFocused()

    // Filter zuruecksetzen
    await page.getByRole('button', { name: exact(t('audit.filterReset')) }).first().click()
    await expect(page).not.toHaveURL(/[?&]zone=/)
  })
})
