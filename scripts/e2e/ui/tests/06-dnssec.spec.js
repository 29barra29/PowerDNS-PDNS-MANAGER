// DNSSEC (F4-B): Aktivieren mit Optionen (NSEC statt NSEC3) inkl. Peer-Warnung, DS-Assistent mit Kopieren,
// Schluessel-Tabelle, Rollover-Assistent Schritt 1 (neuer Schluessel vorab veroeffentlicht); Zone anlegen mit
// DNSSEC-Optionen (KSK+ZSK) auf ns1+ns2. Quellen: WS-F4-B Abschnitt 6 Nr. 1/2/4/6/11, W0-INT-FE2 (DS-Modal).
// Die echte DNSKEY-/Parent-DS-Pruefung (WS-F4-C, Welle 3) deckt 16-pending-wave3.spec.js ab.
const { test, expect } = require('../fixtures/test')
const { uniqueZone } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { zonePath, dialog, modal, field } = require('../fixtures/ui')

const bare = (zone) => zone.replace(/\.$/, '')

test.describe('DNSSEC', () => {
  const zones = []
  test.afterAll(async ({ adminApi }) => {
    for (const z of zones) await adminApi.deleteZone(z)
  })

  test('Aktivieren mit Optionen, DS-Assistent, Schluessel, Rollover Schritt 1', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dnssec')
    zones.push(zone)
    await adminApi.createZone(zone)

    await page.goto(zonePath(zone))
    const card = page.getByRole('region', { name: t('dnssec.cardTitle') })
    await expect(card.getByText(exact(t('dnssec.stateOff')))).toBeVisible()
    await card.getByRole('button', { name: t('dnssec.btnEnable') }).click()

    const enable = dialog(page, t('dnssec.enableTitle', { zone: bare(zone) }))
    await expect(enable).toBeVisible()
    // Zone liegt auch auf ns2 (eigene Datenbank): Warnung, dass nur ns1 signiert wird
    await expect(enable.getByText(t('dnssec.peerWarnTitle'))).toBeVisible()
    await enable.locator('label').filter({ hasText: t('dnssec.denialNsecHint') }).locator('input[type="radio"]').check()
    await enable.getByRole('button', { name: t('dnssec.enableSubmit') }).click()

    // Erfolg: DS-Assistent oeffnet sich, eine DS-Zeile laesst sich kopieren
    const ds = dialog(page, t('zoneDetail.dnssecModalTitle'))
    await expect(ds).toBeVisible()
    await expect(ds.getByText(t('zoneDetail.dnssecModalRecommended'))).toBeVisible()
    // UI-SMOKE-2 (behoben): der sichtbare Text ist der zugaengliche Name (WCAG 2.5.3 Label in Name)
    await ds.getByRole('button', { name: exact(t('zoneDetail.dnssecModalCopyDsLine')) }).first().click()
    await expect(ds.getByText(t('zoneDetail.dnssecCopied'))).toBeVisible()
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    // DS-RDATA (Key-Tag, Algorithmus 13, Digest-Typ 2 = SHA-256, Digest)
    expect(clip).toMatch(/^\d+ 13 2 [0-9a-fA-F]{64}$/)
    await ds.getByRole('button', { name: exact(t('common.close')) }).last().click()
    await expect(ds).toBeHidden()

    await expect(page.getByText(t('zoneDetail.dnssecEnabledOk'))).toBeVisible()
    await expect(card.getByText(exact(t('dnssec.stateSigned')))).toBeVisible()
    await expect(card.getByText('ECDSAP256SHA256 (13)')).toBeVisible()
    await expect(card.getByText(t('dnssec.keyModelCsk'))).toBeVisible()
    await expect(card.getByText(exact(t('dnssec.denialNsec')))).toBeVisible()

    // Schluessel-Tabelle
    await card.getByRole('button', { name: t('dnssec.keysTitle', { count: 1 }) }).click()
    const keyRows = card.locator('tbody tr')
    await expect(keyRows).toHaveCount(1)
    await expect(keyRows.first()).toContainText(t('dnssec.badgeActive'))

    // Rollover-Assistent, Schritt 1: neuer Schluessel wird vorab veroeffentlicht
    await card.getByRole('button', { name: t('dnssec.btnRollover') }).click()
    const roll = dialog(page, t('dnssec.rolloverTitle', { zone: bare(zone) }))
    await expect(roll.getByText(t('dnssec.rolloverIntro'))).toBeVisible()
    await roll.getByRole('button', { name: t('dnssec.rolloverStartButton') }).click()
    await expect(roll.getByText(t('dnssec.rolloverPhaseLabel', { phase: t('dnssec.phase.newPrepublished') }))).toBeVisible()
    await expect(roll.getByText(t('dnssec.rolloverNewDsTitle'))).toBeVisible()
    await roll.getByRole('button', { name: exact(t('common.close')) }).last().click()
    await expect(roll).toBeHidden()
    await expect(card.getByText(exact(t('dnssec.badgeRoleNew')))).toBeVisible()
    await expect(card.locator('tbody tr')).toHaveCount(2)

    const status = await adminApi.get(`dnssec/ns1/${encodeURIComponent(zone)}/status`)
    expect((status.keys || []).length).toBe(2)
  })

  test('Zone mit DNSSEC-Optionen anlegen: ns1 signiert, ns2 Hinweis, Dialog bleibt bis "Fertig"', async ({ page, adminApi }) => {
    const zone = uniqueZone('ui-dnsnew')
    zones.push(zone)
    await page.goto('/zones')
    await page.getByRole('button', { name: t('zones.newZone') }).click()
    const create = modal(page, t('zones.createZone'))
    await field(create, t('zones.domain')).fill(bare(zone))
    await create.locator('input[placeholder="ns1.example.com"]').fill('ns1.e2e.test')
    await create.locator('label').filter({ hasText: t('zones.enableDnssec') }).locator('input[type="checkbox"]').check()
    await expect(create.getByText(t('zones.dnssecRegistrarTitle'))).toBeVisible()
    await create.getByRole('button', { name: t('zones.dnssecOptions') }).click()
    await create.locator('label').filter({ hasText: t('dnssec.keyModelKskZskHint') }).locator('input[type="radio"]').check()
    await create.getByRole('button', { name: exact(t('settings.create')) }).click()

    await expect(create.getByText(t('zones.resultPerServer'))).toBeVisible()
    const results = create.locator('li')
    await expect(results.filter({ hasText: 'ns1:' })).toContainText(t('zones.created'))
    await expect(results.filter({ hasText: 'ns2:' })).toContainText(t('zones.dnssecSkippedOnServer', { server: 'ns1' }))
    await create.getByRole('button', { name: t('zones.doneClose') }).click()
    await expect(create).toBeHidden()

    const status = await adminApi.get(`dnssec/ns1/${encodeURIComponent(zone)}/status`)
    const types = (status.keys || []).map((k) => String(k.keytype || k.type || '').toLowerCase()).sort()
    expect(types).toEqual(['ksk', 'zsk'])
  })
})
