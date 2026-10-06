// Zonen und Records: Zone im Dialog anlegen (Fan-out ns1+ns2), Records anlegen/bearbeiten/klonen/loeschen,
// Fan-out-Hinweis, roter Banner bei Peer-Fehler, Fehler im Dialog bei Primary-Fehler, Nur-Lese-Nutzer.
// Quellen: W0-INT-FE2 Abschnitt 3, F8b Nr. 14/16/17, F1 9.5 (Lese-Nutzer ohne Checkboxen).
const { test, expect } = require('../fixtures/test')
const { PanelApi, pdns, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { field, zonePath, modal, rowWith, acceptNextConfirm } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('Zonen und Records', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Zone anlegen und Records anlegen, bearbeiten, klonen, loeschen (Fan-out auf ns2)', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-zone')
    zones.push(zone)
    const www = `www.${zone}`

    await page.goto('/zones')
    await page.getByRole('button', { name: t('zones.newZone') }).click()
    const create = modal(page, t('zones.createZone'))
    await expect(create).toBeVisible()
    await field(create, t('zones.domain')).fill(bare(zone))
    const ns = create.locator('input[placeholder="ns1.example.com"]')
    await ns.fill('ns1.e2e.test')
    await create.getByRole('button', { name: exact(t('settings.create')) }).click()
    await expect(page.getByText(t('zones.createdSuccess', { zone: bare(zone) }))).toBeVisible()
    await expect(create).toBeHidden()
    // Fan-out: Zone liegt auf beiden Servern
    for (const srv of ['ns1', 'ns2']) await adminApi.get(`zones/${srv}/${encodeURIComponent(zone)}/detail`)

    await page.getByText(bare(zone), { exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/zones/ns1/${bare(zone).replace(/\./g, '\\.')}`))
    await expect(page.getByText(t('zoneDetail.fanoutInfo', { primary: 'ns1', peers: 'ns2' }))).toBeVisible()

    // Anlegen: A mit zwei Werten
    await page.getByRole('button', { name: t('zoneDetail.addRecord') }).first().click()
    const add = modal(page, t('zoneDetail.addRecord'))
    await field(add, t('zoneDetail.nameRelative')).fill('www')
    await field(add, t('zoneDetail.fieldIpv4')).fill('192.0.2.10')
    await add.getByRole('button', { name: t('zoneDetail.addValue') }).click()
    await field(add, t('zoneDetail.fieldIpv4'), { nth: 1 }).fill('192.0.2.11')
    await add.getByRole('button', { name: exact(t('common.save')) }).click()
    await expect(page.getByText(t('zoneDetail.recordCreated', { type: 'A', name: bare(www) }))).toBeVisible()
    await expect(rowWith(page, '192.0.2.11')).toBeVisible()
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, www, 'A')).toEqual(['192.0.2.10', '192.0.2.11'])

    // Bearbeiten: Typ/Name gesperrt, Wert aendern
    await rowWith(page, '192.0.2.11').getByRole('button', { name: t('zoneDetail.edit') }).click()
    const edit = modal(page, t('zoneDetail.editRecord'))
    await expect(field(edit, t('zoneDetail.recordType'))).toBeDisabled()
    await field(edit, t('zoneDetail.fieldIpv4')).fill('192.0.2.12')
    await edit.getByRole('button', { name: exact(t('common.save')) }).click()
    await expect(page.getByText(t('zoneDetail.recordUpdated', { type: 'A', name: bare(www) }))).toBeVisible()
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, www, 'A')).toEqual(['192.0.2.10', '192.0.2.12'])

    // Klonen: Add-Dialog mit denselben Werten, neuer Name
    await rowWith(page, '192.0.2.12').getByRole('button', { name: t('zoneDetail.clone') }).click()
    const clone = modal(page, t('zoneDetail.addRecord'))
    await expect(field(clone, t('zoneDetail.fieldIpv4'))).toHaveValue('192.0.2.12')
    await field(clone, t('zoneDetail.nameRelative')).fill('mail')
    await clone.getByRole('button', { name: exact(t('common.save')) }).click()
    await expect(page.getByText(t('zoneDetail.recordCreated', { type: 'A', name: `mail.${bare(zone)}` }))).toBeVisible()

    // Loeschen eines Werts mit Rueckfrage (nennt den Wert)
    const question = acceptNextConfirm(page)
    await rowWith(page, '192.0.2.10').getByRole('button', { name: t('zoneDetail.deleteRecord') }).click()
    expect(await question).toContain('192.0.2.10')
    await expect(page.getByText(t('zoneDetail.recordDeleted', { type: 'A', name: bare(www) }))).toBeVisible()
    await expect(rowWith(page, '192.0.2.10')).toHaveCount(0)
    await expect.poll(() => adminApi.rrsetContents('ns2', zone, www, 'A')).toEqual(['192.0.2.12'])
  })

  test('Fehler: Peer lehnt ab -> roter Banner; Primary lehnt ab -> Fehler im Dialog', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-fanout')
    zones.push(zone)
    await adminApi.createZone(zone)
    // Am Panel vorbei: CNAME auf ns2 (Konflikt nur am Peer) bzw. auf beiden Servern (Konflikt am Primary)
    await pdns.replace('ns2', zone, `peer.${zone}`, 'CNAME', [`target.${zone}`])
    for (const srv of ['ns1', 'ns2']) await pdns.replace(srv, zone, `both.${zone}`, 'CNAME', [`target.${zone}`])

    await page.goto(zonePath(zone))
    await page.getByRole('button', { name: t('zoneDetail.addRecord') }).first().click()
    let add = modal(page, t('zoneDetail.addRecord'))
    await field(add, t('zoneDetail.nameRelative')).fill('peer')
    await field(add, t('zoneDetail.fieldIpv4')).fill('192.0.2.20')
    await add.getByRole('button', { name: exact(t('common.save')) }).click()
    const alert = page.getByRole('alert').filter({ hasText: pattern('zoneDetail.fanoutPartialError') })
    await expect(alert).toBeVisible()
    await expect(alert).toContainText('ns2:')
    // Primary hat gespeichert
    await expect(rowWith(page, '192.0.2.20')).toBeVisible()

    await page.getByRole('button', { name: t('zoneDetail.addRecord') }).first().click()
    add = modal(page, t('zoneDetail.addRecord'))
    await field(add, t('zoneDetail.nameRelative')).fill('both')
    await field(add, t('zoneDetail.fieldIpv4')).fill('192.0.2.21')
    await add.getByRole('button', { name: exact(t('common.save')) }).click()
    await expect(add.getByRole('alert')).toContainText(t('zoneDetail.createErrorTitle'))
    await expect(add).toBeVisible()
    // ESC schliesst den Dialog, nichts wurde gespeichert
    await page.keyboard.press('Escape')
    await expect(add).toBeHidden()
    await expect(rowWith(page, '192.0.2.21')).toHaveCount(0)
  })

  test('Nur-Lese-Nutzer: Hinweis, Aktionen gesperrt, keine Auswahl-Checkboxen', async ({ adminApi, openAs }) => {
    const zone = uniqueZone('ui-ro')
    zones.push(zone)
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: `www.${zone}`, type: 'A', contents: ['192.0.2.30'] })
    const user = await adminApi.createUser()
    await adminApi.setUserZones(user.id, { [zone]: 'read' })
    const api = await PanelApi.login(user.username, user.password)
    try {
      const page = await openAs(api)
      await page.goto(zonePath(zone))
      await expect(page.getByText(t('zoneDetail.readOnlyZone'))).toBeVisible()
      await expect(rowWith(page, '192.0.2.30')).toBeVisible()
      await expect(page.getByRole('button', { name: t('zoneDetail.addRecord') }).first()).toBeDisabled()
      await expect(rowWith(page, '192.0.2.30').getByRole('button', { name: t('zoneDetail.edit') })).toBeDisabled()
      await expect(page.getByRole('checkbox', { name: t('bulk.selectRow') })).toHaveCount(0)
      await expect(page.getByText(pattern('zoneDetail.fanoutInfo'))).toHaveCount(0)
    } finally {
      await api.dispose()
      await adminApi.deleteUser(user.id)
    }
  })
})
