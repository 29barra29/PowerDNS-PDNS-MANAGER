// Export (Download der BIND-Zonendatei) und NOTIFY (F2): Master-Zone mit NOTIFY, Native-Zone mit gesperrtem Knopf
// und Tooltip, Lese-Nutzer exportiert, darf aber kein NOTIFY senden. Quelle: WS-F2F3 (Spec 9.3, Browser-Liste).
const fs = require('node:fs')
const { test, expect } = require('../fixtures/test')
const { PanelApi, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

async function exportZone(page, zone) {
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: exact(t('zoneDetail.exportButton')) }).click()
  const file = await download
  const content = fs.readFileSync(await file.path(), 'utf8')
  return { name: file.suggestedFilename(), content }
}

test.describe('Export und NOTIFY', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Master-Zone: Export-Download und NOTIFY; Native-Zone: NOTIFY gesperrt', async ({ page, adminApi }) => {
    const master = uniqueZone('ui-master')
    const native = uniqueZone('ui-native')
    zones.push(master, native)
    await adminApi.createZone(master, { kind: 'Master' })
    await adminApi.createZone(native)
    await adminApi.addRecord('ns1', master, { name: `www.${master}`, type: 'A', contents: ['192.0.2.70'] })

    await page.goto(zonePath(master))
    const { name, content } = await exportZone(page, master)
    expect(name).toContain(bare(master))
    expect(content).toContain('192.0.2.70')
    expect(content).toMatch(/\sSOA\s/)
    await expect(page.getByText(pattern('zoneDetail.exportSuccess'))).toBeVisible()

    const notify = page.getByRole('button', { name: exact(t('zoneDetail.notifyButton')) })
    await expect(notify).toBeEnabled()
    await notify.click()
    const ok = page.getByText(t('zoneDetail.notifySuccess', { zone: bare(master), server: 'ns1' }), { exact: false })
    await expect(ok).toBeVisible()
    await expect(ok).toContainText(t('zoneDetail.notifyOtherServersHint', { servers: 'ns2' }))

    await page.goto(zonePath(native))
    const nativeNotify = page.getByRole('button', { name: exact(t('zoneDetail.notifyButton')) })
    await expect(nativeNotify).toBeDisabled()
    await expect(page.locator('span[title]').filter({ has: nativeNotify }))
      .toHaveAttribute('title', t('zoneDetail.notifyUnsupportedKind', { kind: 'Native' }))
  })

  test('Lese-Nutzer: Export erlaubt, NOTIFY gesperrt', async ({ adminApi, openAs }) => {
    const zone = uniqueZone('ui-exp-ro')
    zones.push(zone)
    await adminApi.createZone(zone, { kind: 'Master' })
    await adminApi.addRecord('ns1', zone, { name: `ro.${zone}`, type: 'TXT', contents: ['"ui-export"'] })
    const user = await adminApi.createUser()
    await adminApi.setUserZones(user.id, { [zone]: 'read' })
    const api = await PanelApi.login(user.username, user.password)
    try {
      const page = await openAs(api)
      await page.goto(zonePath(zone))
      const { content } = await exportZone(page, zone)
      expect(content).toContain('ui-export')
      const notify = page.getByRole('button', { name: exact(t('zoneDetail.notifyButton')) })
      await expect(notify).toBeDisabled()
      await expect(page.locator('span[title]').filter({ has: notify }))
        .toHaveAttribute('title', t('zoneDetail.notifyNoPermission'))
    } finally {
      await api.dispose()
      await adminApi.deleteUser(user.id)
    }
  })
})
