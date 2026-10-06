// DynDNS-Karte (F9F11-FE): Token anlegen (zwei Hostnamen, nur A, TTL, PTR) -> Einmal-Anzeige mit Anleitung
// (FRITZ!Box-URL ohne <pass>, ESC schliesst nicht, Kopieren) -> Liste mit Chips; echtes Update ueber /nic/update mit
// dem Token -> letzter Abruf/Ergebnis in der Karte; Admin-Bereich "Alle DynDNS-Tokens".
// Quellen: WS-F9F11-FE Abschnitt 2 Nr. 1-4.
const { request } = require('@playwright/test')
const { test, expect } = require('../fixtures/test')
const { BASE_URL } = require('../fixtures/env')
const { uniqueZone, unique } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { dialog } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

async function nicUpdate(token, hostname, ip) {
  const ctx = await request.newContext({ baseURL: BASE_URL })
  try {
    const auth = Buffer.from(`fritzbox:${token}`).toString('base64')
    const res = await ctx.get(`/nic/update?hostname=${encodeURIComponent(hostname)}&myip=${ip}`, {
      headers: { Authorization: `Basic ${auth}` },
    })
    return (await res.text()).trim()
  } finally {
    await ctx.dispose()
  }
}

test.describe('DynDNS', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    const data = await adminApi.get('dyndns/tokens').catch(() => null)
    for (const tok of data?.tokens || []) {
      if (String(tok.name || '').startsWith('ui-dyn')) await adminApi.del(`dyndns/tokens/${tok.id}`).catch(() => {})
    }
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Token anlegen mit Anleitung, Update per /nic/update, Liste und Admin-Uebersicht', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dyn')
    zones.push(zone)
    await adminApi.createZone(zone)
    const name = unique('ui-dyn')

    await page.goto('/settings?tab=integrations')
    const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('dyndns.title') }) })
    await expect(card.getByText(t('dyndns.disabledByAdmin'))).toHaveCount(0)
    await card.getByRole('button', { name: t('dyndns.createToken') }).click()

    const form = dialog(page, t('dyndns.createToken'))
    await form.getByLabel(t('dyndns.fieldName'), { exact: true }).fill(name)
    await form.getByLabel(t('dyndns.fieldZone'), { exact: true }).fill(bare(zone))
    for (const sub of ['home', 'nas']) {
      await form.getByLabel(t('dyndns.fieldSubdomain'), { exact: true }).fill(sub)
      await form.getByLabel(t('dyndns.fieldSubdomain'), { exact: true }).press('Enter')
      await expect(form.getByRole('list', { name: t('dyndns.fieldHostnames') })).toContainText(`${sub}.${bare(zone)}`)
    }
    const aaaa = form.locator('label').filter({ hasText: /^\s*AAAA\s*$/ }).locator('input[type="checkbox"]')
    if (await aaaa.isChecked()) await aaaa.uncheck()
    await form.getByLabel(t('dyndns.fieldTtl'), { exact: true }).fill('300')
    await form.getByRole('button', { name: exact(t('dyndns.createToken')) }).click()

    // Einmal-Anzeige mit Anleitung
    const once = dialog(page, t('dyndns.plaintextTitle'))
    await expect(once).toBeVisible()
    const token = (await once.getByLabel(t('secretModal.secretLabel')).innerText()).trim()
    expect(token.length).toBeGreaterThan(20)
    await expect(once.getByRole('heading', { name: t('dyndns.guideTitle') })).toBeVisible()
    await expect(once.getByRole('heading', { name: t('dyndns.guideFritzTitle') })).toBeVisible()
    const guide = await once.innerText()
    // FRITZ!Box-Update-URL: Platzhalter der Box (<domain>, <ipaddr>), nie Zugangsdaten (<pass>/<username>)
    const urls = guide.split('\n').filter((l) => l.includes('/nic/update?hostname='))
    expect(urls.length).toBeGreaterThan(0)
    for (const line of urls) expect(line).not.toMatch(/<pass>|<username>/)
    expect(guide).toContain(`home.${bare(zone)}`)
    await page.keyboard.press('Escape')
    await expect(once).toBeVisible()
    await once.getByRole('button', { name: t('secretModal.copy') }).first().click()
    await expect(once.getByText(t('secretModal.copied'))).toBeVisible()
    await once.getByRole('button', { name: t('dyndns.plaintextDone') }).click()
    await expect(once).toBeHidden()
    await expect(card.getByText(t('dyndns.created'))).toBeVisible()

    // Echtes Update mit dem Token (wie eine FRITZ!Box)
    expect(await nicUpdate(token, `home.${bare(zone)}`, '192.0.2.80')).toBe('good 192.0.2.80')
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, `home.${zone}`, 'A')).toEqual(['192.0.2.80'])

    // Liste: Chips der Hostnamen, letzter Abruf nach Neuladen
    await page.reload()
    const item = card.locator('li').filter({ has: page.getByRole('button', { name: `${t('common.edit')}: ${name}` }) })
    const chips = card.getByRole('list', { name: t('dyndns.colHostnames') }).filter({ hasText: `home.${bare(zone)}` })
    await expect(chips).toContainText(`nas.${bare(zone)}`)
    await expect(item).toContainText('192.0.2.80')
    await expect(item).not.toContainText(t('dyndns.never'))

    // Admin: alle DynDNS-Tokens
    await card.getByRole('button', { name: t('dyndns.adminAllTokens') }).click()
    await expect(card.locator('table').getByText(name)).toBeVisible()
  })
})
