// Zonenverlauf (F7): Deep-Link ?tab=history, Record-Filter aus der Zeilen-Aktion, Rollback-Dialog (Vorschau, ESC,
// Fokus-Rueckgabe), Rollback ohne Konflikt und mit Konflikt (Pflicht-Checkbox -> force), Lese-Nutzer ohne Knopf.
// Quellen: F7 9.4 (WS-F7-FE A4), W1-NACHARBEIT 5.6 (Tastatur).
const { test, expect } = require('../fixtures/test')
const { PanelApi, uniqueZone } = require('../fixtures/api')
const { t, pattern } = require('../fixtures/i18n')
const { zonePath, rowWith, dialog, expectFocusInside } = require('../fixtures/ui')


async function historyEntries(api, zone) {
  const data = await api.get(`zones/ns1/${encodeURIComponent(zone)}/history?limit=50`)
  return data?.entries || []
}

test.describe('Zonenverlauf und Rollback', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Deep-Link, Record-Filter, Rollback ohne und mit Konflikt', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-hist')
    zones.push(zone)
    const www = `www.${zone}`
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: www, type: 'A', contents: ['192.0.2.50'] })
    await adminApi.put(`records/ns1/${encodeURIComponent(zone)}`, {
      name: www, type: 'A', ttl: 300, old_content: '192.0.2.50', new_content: '192.0.2.51',
    })
    const entries = await historyEntries(adminApi, zone)
    const update = entries.find((e) => e.action === 'UPDATE' && e.resource_name === www)
    const create = entries.find((e) => e.action === 'CREATE' && e.resource_name === www)
    expect(update && create, `Verlaufseintraege: ${JSON.stringify(entries.map((e) => [e.id, e.action]))}`).toBeTruthy()

    // Zeilen-Aktion "Verlauf" -> Tab Verlauf mit Record-Filter
    await page.goto(zonePath(zone))
    await rowWith(page, '192.0.2.51').getByRole('button', { name: t('zoneDetail.recordHistoryTitle') }).click()
    await expect(page.getByRole('tab', { name: t('zoneDetail.tabHistory') })).toHaveAttribute('aria-selected', 'true')
    await expect(page).toHaveURL(/tab=history/)
    await expect(page.getByText(t('history.recordFilter', { name: 'www', type: 'A' }))).toBeVisible()

    // Deep-Link direkt auf den Verlauf
    await page.goto(`${zonePath(zone)}?tab=history`)
    await expect(page.getByRole('heading', { name: t('history.title') })).toBeVisible()
    const updateCard = page.locator('li').filter({ hasText: `#${update.id}` })
    await expect(updateCard).toBeVisible()

    // Rollback-Dialog: Fokus im Dialog, ESC schliesst, Fokus zurueck auf dem Knopf
    const rollbackBtn = updateCard.getByRole('button', { name: t('history.rollback') })
    await rollbackBtn.click()
    let modal = dialog(page, t('history.rollbackTitle', { id: update.id }))
    await expect(modal).toBeVisible()
    await expect(modal.getByText(t('history.rollbackIntro', { server: 'ns1' }))).toBeVisible()
    await expectFocusInside(modal)
    await page.keyboard.press('Escape')
    await expect(modal).toBeHidden()
    await expect(rollbackBtn).toBeFocused()

    // Rollback ohne Konflikt
    await rollbackBtn.click()
    modal = dialog(page, t('history.rollbackTitle', { id: update.id }))
    await modal.getByRole('button', { name: t('history.rollbackConfirm') }).click()
    await expect(modal).toBeHidden()
    await expect(page.getByText(pattern('history.rollbackDone'))).toContainText(`#${update.id}`)
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, www, 'A')).toEqual(['192.0.2.50'])

    // Konflikt: Record nach dem Anlegen am Panel vorbei geaendert -> Rollback des CREATE nur mit Bestaetigung
    await adminApi.put(`records/ns1/${encodeURIComponent(zone)}`, {
      name: www, type: 'A', ttl: 300, old_content: '192.0.2.50', new_content: '192.0.2.52',
    })
    await page.getByRole('button', { name: t('history.refresh') }).click()
    const createCard = page.locator('li').filter({ hasText: `#${create.id}` })
    await createCard.getByRole('button', { name: t('history.rollback') }).click()
    modal = dialog(page, t('history.rollbackTitle', { id: create.id }))
    await expect(modal.getByText(t('history.rollbackConflictTitle'))).toBeVisible()
    const confirm = modal.getByRole('button', { name: t('history.rollbackConfirm') })
    await expect(confirm).toBeDisabled()
    await modal.getByRole('checkbox', { name: t('history.rollbackForce') }).check()
    await confirm.click()
    await expect(modal).toBeHidden()
    await expect.poll(() => adminApi.rrsetContents('ns1', zone, www, 'A')).toEqual([])
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, www, 'A')).toEqual([])
  })

  test('Lese-Nutzer sieht den Verlauf ohne Rollback-Knopf', async ({ adminApi, openAs }) => {
    const zone = uniqueZone('ui-hist-ro')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: `www.${zone}`, type: 'A', contents: ['192.0.2.60'] })
    const user = await adminApi.createUser()
    await adminApi.setUserZones(user.id, { [zone]: 'read' })
    const api = await PanelApi.login(user.username, user.password)
    try {
      const page = await openAs(api)
      await page.goto(`${zonePath(zone)}?tab=history`)
      await expect(page.getByRole('heading', { name: t('history.title') })).toBeVisible()
      // Eintraege nennen Namen relativ zur Zone ("www A")
      await expect(page.locator('li').filter({ hasText: 'www A' }).first()).toBeVisible()
      await expect(page.getByRole('button', { name: t('history.rollback') })).toHaveCount(0)
    } finally {
      await api.dispose()
      await adminApi.deleteUser(user.id)
    }
  })
})
