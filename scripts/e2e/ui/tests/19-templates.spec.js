// Vorlagen (FE1b Nr. 8, F8b Nr. 7): Vorlage mit Record anlegen (TTL ausserhalb des Bereichs -> Fehler im Dialog),
// beim Anlegen einer Zone auswaehlen (Vorschau, "(1 Eintrag)"), Records landen in der Zone; Vorlage loeschen.
const { test, expect } = require('../fixtures/test')
const { unique, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { field, modal, acceptNextConfirm } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Vorlagen', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Vorlage anlegen, in der Zonenanlage verwenden, loeschen', async ({ page, adminApi }) => {
    const name = unique('ui-tpl')
    const zone = uniqueZone('ui-tpl')
    zones.push(zone)

    await page.goto('/settings?tab=templates')
    await page.getByRole('main').getByRole('button', { name: t('templates.newTemplate') }).first().click()
    const form = modal(page, t('templates.createNewTemplate'))
    await field(form, t('templates.templateName')).fill(name)
    // Record-Zeile: www A 192.0.2.150, TTL zunaechst ungueltig
    await field(form, t('zoneDetail.name')).fill('www')
    await field(form, t('templates.recordLabelA')).fill('192.0.2.150')
    await form.locator('#template-record-ttl').fill('30')
    await form.getByRole('button', { name: t('settings.add') }).click()
    await expect(form.getByText(pattern('ttlInput.outOfRange')).first()).toBeVisible()
    await form.locator('#template-record-ttl').fill('300')
    await form.getByRole('button', { name: t('settings.add') }).click()
    await expect(form.getByText('192.0.2.150')).toBeVisible()
    await form.getByRole('button', { name: exact(t('templates.createButton')) }).click()
    await expect(page.getByText(t('settings.templateCreated'))).toBeVisible()
    await expect(page.locator('.glass-card').filter({ hasText: name }).first()).toBeVisible()

    // Zone mit Vorlage anlegen
    await page.goto('/zones')
    await page.getByRole('button', { name: t('zones.newZone') }).click()
    const create = modal(page, t('zones.createZone'))
    const tplSelect = field(create, t('zones.template'))
    const option = tplSelect.locator('option').filter({ hasText: name })
    await expect(option).toContainText(t('zones.templateRecordCount', { count: 1 }))
    await tplSelect.selectOption({ label: (await option.innerText()).trim() })
    await expect(create.getByText(t('zones.templatePreviewTitle'))).toBeVisible()
    await field(create, t('zones.domain')).fill(bare(zone))
    const ns = create.locator('input[placeholder="ns1.example.com"]')
    if (!(await ns.inputValue())) await ns.fill('ns1.e2e.test')
    await create.getByRole('button', { name: exact(t('settings.create')) }).click()
    await expect(page.getByText(t('zones.createdSuccess', { zone: bare(zone) }))).toBeVisible()
    await expect.poll(() => adminApi.rrsetContents('ns1', zone, `www.${zone}`, 'A')).toEqual(['192.0.2.150'])

    // Vorlage loeschen
    await page.goto('/settings?tab=templates')
    const card = page.locator('.glass-card').filter({ hasText: name }).filter({ has: page.getByTitle(t('templates.deleteTitle')) }).last()
    const confirm = acceptNextConfirm(page)
    await card.getByTitle(t('templates.deleteTitle')).click()
    expect(await confirm).toContain(name)
    await expect(page.getByText(t('settings.templateDeleted', { name }))).toBeVisible()
  })
})
