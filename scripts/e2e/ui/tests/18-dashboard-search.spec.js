// Uebersicht und Suche (F8b Nr. 10/20): Dashboard zeigt beide PowerDNS-Server online; die Suche findet einen Record,
// die Spalte "Zone" verlinkt in die Zonenansicht; Nicht-Admin findet nur Treffer in eigenen Zonen.
const { test, expect } = require('../fixtures/test')
const { PanelApi, uniqueZone, unique } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Uebersicht und Suche', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Dashboard: beide Server online, Zonen-Zaehler', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: t('dashboard.overview') })).toBeVisible()
    for (const srv of ['ns1', 'ns2']) {
      const card = page.locator('.glass-card').filter({ hasText: srv }).filter({ hasText: t('dashboard.daemonVersion') })
      await expect(card.first()).toContainText(t('dashboard.online'))
    }
    await expect(page.getByText(t('dashboard.offline'), { exact: true })).toHaveCount(0)
  })

  test('Suche: Treffer mit Link in die Zone; Nicht-Admin nur eigene Zonen', async ({ page, adminApi, openAs }) => {
    const zone = uniqueZone('ui-search')
    const hidden = uniqueZone('ui-search-x')
    zones.push(zone, hidden)
    const label = unique('suchwert')
    await adminApi.createZone(zone)
    await adminApi.createZone(hidden)
    await adminApi.addRecord('ns1', zone, { name: `${label}.${zone}`, type: 'TXT', contents: [`"${label}"`] })
    await adminApi.addRecord('ns1', hidden, { name: `${label}.${hidden}`, type: 'TXT', contents: [`"${label}"`] })

    await page.goto('/search')
    await page.getByPlaceholder(t('search.placeholder')).fill(label)
    await page.getByRole('button', { name: exact(t('search.button')) }).click()
    const rows = page.locator('tbody tr').filter({ hasText: `${label}.${bare(zone)}` })
    await expect(rows.first()).toBeVisible()
    await expect(page.locator('tbody tr').filter({ hasText: `${label}.${bare(hidden)}` }).first()).toBeVisible()
    await rows.first().getByRole('link', { name: bare(zone) }).click()
    await expect(page).toHaveURL(new RegExp(`/zones/ns[12]/${bare(zone).replace(/\./g, '\\.')}`))
    await expect(page.getByRole('heading', { name: bare(zone) })).toBeVisible()

    // Nicht-Admin mit Leserecht auf eine Zone findet nur dort
    const user = await adminApi.createUser()
    await adminApi.setUserZones(user.id, { [zone]: 'read' })
    const api = await PanelApi.login(user.username, user.password)
    try {
      const upage = await openAs(api)
      await upage.goto('/search')
      await upage.getByPlaceholder(t('search.placeholder')).fill(label)
      await upage.getByPlaceholder(t('search.placeholder')).press('Enter')
      await expect(upage.locator('tbody tr').filter({ hasText: `${label}.${bare(zone)}` }).first()).toBeVisible()
      await expect(upage.locator('tbody tr').filter({ hasText: bare(hidden) })).toHaveCount(0)
    } finally {
      await api.dispose()
      await adminApi.deleteUser(user.id)
    }
  })
})
