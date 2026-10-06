// Panel-Tokens mit Scope (F14-APP): Nicht-Admin legt einen Lese-Token fuer eine ausgewaehlte Zone an
// (Einmal-Anzeige), der Token wirkt nur dort und nur lesend; Bearbeiten ohne Aenderung sendet nichts, Namensaenderung
// sendet nur den Namen; Pausieren/Aktivieren/Widerrufen wirken sofort. Admin: "Admin-Rechte" nur bei "alle Zonen".
// Quelle: WS-F14-APP 5 (Pruefliste F14 9.7).
const { request } = require('@playwright/test')
const { test, expect } = require('../fixtures/test')
const { BASE_URL } = require('../fixtures/env')
const { PanelApi, unique, uniqueZone, enc } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { dialog, acceptNextConfirm } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

async function withToken(token, method, path, data) {
  const ctx = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Authorization: `Bearer ${token}` } })
  try {
    const res = await ctx.fetch(`/api/v1/${path}`, { method, data })
    return res.status()
  } finally {
    await ctx.dispose()
  }
}

test.describe('Panel-Tokens', () => {
  const zones = []
  const users = []
  test.afterAll(async ({ adminApi }) => {
    for (const id of users) await adminApi.deleteUser(id)
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Nicht-Admin: Lese-Token fuer eine Zone, Bearbeiten, Pausieren, Widerrufen', async ({ adminApi, openAs }) => {
    const mine = uniqueZone('ui-tok')
    const other = uniqueZone('ui-tok-x')
    zones.push(mine, other)
    await adminApi.createZone(mine)
    await adminApi.createZone(other)
    const user = await adminApi.createUser()
    users.push(user.id)
    await adminApi.setUserZones(user.id, { [mine]: 'manage' })
    const api = await PanelApi.login(user.username, user.password)
    const name = unique('ui-tok')
    try {
      const page = await openAs(api)
      await page.goto('/settings?tab=integrations')
      const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('settings.integrations.panelTokens') }) })
      await card.getByRole('button', { name: t('panelTokens.create') }).first().click()

      const form = dialog(page, t('panelTokens.modalCreateTitle'))
      await expect(form).toBeVisible()
      await form.getByLabel(t('panelTokens.fieldName')).fill(name)
      // Nicht-Admin: "Alle meine Zonen" statt "Alle Zonen"; Admin-Rechte gibt es nicht
      await expect(form.locator('label').filter({ hasText: t('panelTokens.zonesAllMine') })).toBeVisible()
      await expect(form.getByText(t('panelTokens.allowAdmin'))).toHaveCount(0)
      await form.locator('label').filter({ hasText: t('panelTokens.zonesSelected') }).locator('input[type="radio"]').check()
      const zoneBox = form.locator('label').filter({ hasText: bare(mine) }).locator('input[type="checkbox"]')
      await zoneBox.check()
      await expect(form.locator('label').filter({ hasText: bare(other) })).toHaveCount(0)
      await form.locator('label').filter({ hasText: t('panelTokens.permReadHint') }).locator('input[type="radio"]').check()
      await form.getByLabel(t('panelTokens.expiry')).selectOption('30')
      await form.getByRole('button', { name: exact(t('panelTokens.submitCreate')) }).click()

      const once = dialog(page, t('panelTokens.createdTitle'))
      const token = (await once.getByLabel(t('secretModal.secretLabel')).innerText()).trim()
      await once.getByRole('button', { name: t('secretModal.done') }).click()
      await expect(once).toBeHidden()
      await expect(card.getByText(t('panelTokens.created', { name }))).toBeVisible()
      const row = card.locator('tr').filter({ hasText: name })
      await expect(row).toContainText(t('panelTokens.permRead'))
      await expect(row).toContainText(t('panelTokens.zoneCount', { count: 1 }))

      // Wirkung: nur die eigene Zone, nur lesend
      expect(await withToken(token, 'GET', `records/ns1/${enc(mine)}`)).toBe(200)
      expect(await withToken(token, 'POST', `records/ns1/${enc(mine)}`, { name: `x.${mine}`, type: 'A', ttl: 300, records: [{ content: '192.0.2.1' }] })).toBe(403)
      expect(await withToken(token, 'GET', `records/ns1/${enc(other)}`)).toBe(403)

      // Bearbeiten ohne Aenderung: kein Request; nur Name geaendert: PUT nur mit name
      const puts = []
      page.on('request', (req) => { if (req.method() === 'PUT' && req.url().includes('/auth/me/panel-tokens/')) puts.push(req.postDataJSON()) })
      await row.getByRole('button', { name: `${t('panelTokens.edit')}: ${name}` }).click()
      let edit = dialog(page, t('panelTokens.modalEditTitle'))
      await edit.getByRole('button', { name: exact(t('common.save')) }).click()
      await expect(edit).toBeHidden()
      expect(puts).toEqual([])
      await row.getByRole('button', { name: `${t('panelTokens.edit')}: ${name}` }).click()
      edit = dialog(page, t('panelTokens.modalEditTitle'))
      await edit.getByLabel(t('panelTokens.fieldName')).fill(`${name}-neu`)
      await edit.getByRole('button', { name: exact(t('common.save')) }).click()
      await expect(card.getByText(t('panelTokens.saved', { name: `${name}-neu` }))).toBeVisible()
      expect(puts).toEqual([{ name: `${name}-neu` }])

      // Pausieren -> Token gesperrt; Aktivieren -> wieder gueltig
      const row2 = card.locator('tr').filter({ hasText: `${name}-neu` })
      let confirm = acceptNextConfirm(page)
      await row2.getByRole('button', { name: `${t('panelTokens.pause')}: ${name}-neu` }).click()
      await confirm
      await expect(row2).toContainText(t('panelTokens.statusPaused'))
      expect(await withToken(token, 'GET', `records/ns1/${enc(mine)}`)).toBe(401)
      await row2.getByRole('button', { name: `${t('panelTokens.resume')}: ${name}-neu` }).click()
      await expect(row2).toContainText(t('panelTokens.statusActive'))
      expect(await withToken(token, 'GET', `records/ns1/${enc(mine)}`)).toBe(200)

      // Widerrufen
      confirm = acceptNextConfirm(page)
      await row2.getByRole('button', { name: `${t('panelTokens.revoke')}: ${name}-neu` }).click()
      await confirm
      await expect(card.getByText(t('panelTokens.revoked', { name: `${name}-neu` }))).toBeVisible()
      await expect(card.locator('tr').filter({ hasText: `${name}-neu` })).toHaveCount(0)
      expect(await withToken(token, 'GET', `records/ns1/${enc(mine)}`)).toBe(401)
    } finally {
      await api.dispose()
    }
  })

  test('Admin: Admin-Rechte nur mit "Alle Zonen" waehlbar', async ({ page }) => {
    await page.goto('/settings?tab=integrations')
    const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('settings.integrations.panelTokens') }) })
    await card.getByRole('button', { name: t('panelTokens.create') }).first().click()
    const form = dialog(page, t('panelTokens.modalCreateTitle'))
    const allowAdmin = form.locator('label').filter({ hasText: t('panelTokens.allowAdmin') }).locator('input[type="checkbox"]')
    // Standard beim Anlegen: "Nur ausgewaehlte Zonen" -> Admin-Rechte gesperrt; mit "Alle Zonen" waehlbar
    await expect(allowAdmin).toBeDisabled()
    await form.locator('label').filter({ hasText: t('panelTokens.zonesAllAdminHint') }).locator('input[type="radio"]').check()
    await expect(allowAdmin).toBeEnabled()
    await form.locator('label').filter({ hasText: t('panelTokens.zonesSelected') }).locator('input[type="radio"]').check()
    await expect(allowAdmin).toBeDisabled()
    await expect(form.getByText(t('panelTokens.allowAdminNeedsAllZones'))).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(form).toBeHidden()
  })
})
