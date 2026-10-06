// Bulk-Editor (F1): Auswahl mit Indeterminate-Kopfzeile, TTL fuer die Auswahl -> Vorschau -> Anwenden; Text-Editor mit
// Zeilenfehler, Vorschau, Anwenden mit PTR-Pflege (Erfolgszeile + gelber Hinweis bei Konflikt).
// Quellen: F1 9.5 (Notiz WS-F1 4), WS-F1-fix2 (Antrag an WS-UI-SMOKE), F9F11-FE Nr. 6.
const { test, expect } = require('../fixtures/test')
const { uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { zonePath, rowWith, dialog } = require('../fixtures/ui')


const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Bulk-Editor', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Auswahl (Indeterminate) -> TTL setzen -> Vorschau -> Anwenden', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-bulk')
    zones.push(zone)
    const www = `www.${zone}`
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: www, type: 'A', ttl: 300, contents: ['192.0.2.40', '192.0.2.41'] })

    await page.goto(zonePath(zone))
    await expect(rowWith(page, '192.0.2.41')).toBeVisible()
    const header = page.getByRole('checkbox', { name: t('bulk.selectAll', { type: 'A' }) })

    // Ein Wert gewaehlt -> Kopf-Checkbox "teilweise"
    await rowWith(page, '192.0.2.40').getByRole('checkbox', { name: t('bulk.selectRow') }).check()
    const toolbar = page.getByRole('toolbar', { name: t('bulk.toolbarLabel') })
    await expect(toolbar).toContainText(t('bulk.selectedCount', { count: 1 }))
    expect(await header.evaluate((el) => el.indeterminate)).toBe(true)
    // SOA ist nicht waehlbar
    const soaCard = page.locator('.glass-card').filter({ has: page.locator('span', { hasText: /^SOA$/ }) })
    await expect(soaCard.getByRole('checkbox', { name: t('bulk.selectRow') })).toBeDisabled()

    await header.check()
    await expect(toolbar).toContainText(t('bulk.selectedCount', { count: 2 }))
    expect(await header.evaluate((el) => el.indeterminate)).toBe(false)

    await toolbar.getByRole('button', { name: t('bulk.actionSetTtl') }).click()
    const ttlDialog = dialog(page, t('bulk.ttlDialogTitle'))
    await expect(ttlDialog).toBeVisible()
    await ttlDialog.getByLabel(t('bulk.ttlLabel')).fill('600')
    await ttlDialog.getByRole('button', { name: t('bulk.continueToPreview') }).click()

    const editor = page.getByRole('dialog', { name: t('bulk.previewTitle') })
    await expect(editor).toBeVisible()
    // Vorschau zeigt Namen relativ zur Zone
    await expect(editor.getByRole('row', { name: /^www A / })).toContainText('300 → 600')
    await editor.getByRole('button', { name: t('bulk.apply', { count: 1 }) }).click()
    await expect(page.getByText(t('bulk.applied', { count: 1 }))).toBeVisible()
    await expect(toolbar).toBeHidden()

    await expect.poll(async () => {
      const recs = (await adminApi.records('ns2', zone)).filter((r) => r.name === www && r.type === 'A')
      return recs.map((r) => r.ttl)
    }).toEqual([600, 600])
  })

  test('Text-Editor: Zeilenfehler, Vorschau, Anwenden mit PTR-Pflege', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-text')
    const octet = 1 + Math.floor(Math.random() * 250)
    const rev = `${octet}.77.10.in-addr.arpa.`
    zones.push(zone, rev)
    await adminApi.createZone(zone)
    await adminApi.createZone(rev)
    // Fremder PTR fuer .99 -> wird nicht ueberschrieben (gelber Hinweis)
    await adminApi.addRecord('ns1', rev, { name: `99.${rev}`, type: 'PTR', contents: ['other.example.com.'] })
    const ipNew = `10.77.${octet}.20`
    const ipClash = `10.77.${octet}.99`

    await page.goto(zonePath(zone))
    await page.getByRole('button', { name: t('bulk.openTextEditor') }).click()
    // Der Dialogtitel wechselt mit dem Schritt (Editor -> Vorschau); daher ohne Titel-Filter
    const editor = page.getByRole('dialog')
    await expect(editor).toHaveAccessibleName(t('bulk.editorTitle'))
    const text = editor.locator('textarea')
    await text.fill(`api.${zone} 300 IN A ${ipNew}\ndas ist keine Record-Zeile\n`)
    await editor.getByRole('button', { name: exact(t('bulk.preview')) }).click()
    const parseErrors = editor.getByRole('alert').filter({ hasText: t('bulk.parseErrorsTitle') })
    await expect(parseErrors).toBeVisible()
    await expect(parseErrors).toContainText(t('bulk.lineLabel', { line: 2 }))

    await text.fill(`api.${zone} 300 IN A ${ipNew}\nclash.${zone} 300 IN A ${ipClash}\n`)
    await editor.getByRole('button', { name: exact(t('bulk.preview')) }).click()
    await expect(editor).toHaveAccessibleName(t('bulk.previewTitle'))
    await expect(editor.getByText(t('bulk.semanticsMergeBanner'))).toBeVisible()
    await expect(editor.getByRole('row', { name: /^api A / })).toBeVisible()
    await expect(editor.getByRole('row', { name: /^clash A / })).toBeVisible()
    await expect(editor.getByText(t('bulk.fanoutNote', { primary: 'ns1', peers: 'ns2' }))).toBeVisible()

    await editor.getByRole('checkbox', { name: t('ptr.manage') }).check()
    await editor.getByRole('button', { name: t('bulk.apply', { count: 2 }) }).click()
    await expect(editor).toBeHidden()

    // Erfolg inkl. gesetztem PTR; Konflikt als gelber Hinweis mit der IP
    const success = page.getByText(pattern('bulk.applied'))
    await expect(success).toBeVisible()
    await expect(success).toContainText(t('ptr.resultSet', { ptr: `20.${bare(rev)}`, target: `api.${bare(zone)}` }))
    const warning = page.getByRole('status').filter({ hasText: t('ptr.warningTitle') })
    await expect(warning).toBeVisible()
    await expect(warning).toContainText(ipClash)
    await expect(rowWith(page, ipNew)).toBeVisible()

    await expect.poll(() => adminApi.rrsetContents('ns1', rev, `20.${rev}`, 'PTR')).toEqual([`api.${zone}`])
    await expect.poll(() => adminApi.rrsetContents('ns1', rev, `99.${rev}`, 'PTR')).toEqual(['other.example.com.'])
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, `clash.${zone}`, 'A')).toEqual([ipClash])
  })
})
