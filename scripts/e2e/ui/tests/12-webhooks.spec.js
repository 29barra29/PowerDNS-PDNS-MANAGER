// Webhooks-Karte (F6-FE): Anlegen (Fokus im Formular), Secret-Einmal-Anzeige (ESC schliesst nicht, Tab bleibt im
// Dialog), "Test senden", echte Zustellung an den E2E-Empfaenger nach einer Record-Aenderung, Zustellprotokoll
// (Status "Zugestellt", Details), ESC schliesst das Protokoll und gibt den Fokus zurueck; unlesbare Ziel-URL
// (Badge, Test gesperrt, Bearbeiten verlangt eine neue URL).
// Quellen: WS-F6-FE (F6 9.3), W1-NACHARBEIT 5.6 (Tastatur-Checkliste).
const { test, expect } = require('../fixtures/test')
const { receiver, unique, uniqueZone } = require('../fixtures/api')
const { t, exact, pattern } = require('../fixtures/i18n')
const { dialog, expectFocusInside, expectFocusTrapped } = require('../fixtures/ui')
const { db } = require('../fixtures/db')

test.describe('Webhooks', () => {
  const cleanup = []
  test.afterAll(async ({ adminApi }) => {
    for (const fn of cleanup) await fn(adminApi).catch(() => {})
  })

  test('Anlegen, Secret, Test, echte Zustellung und Zustellprotokoll', async ({ page, adminApi }) => {
    const name = unique('ui-hook')
    const zone = uniqueZone('ui-hook')
    cleanup.push((api) => api.deleteZone(zone))
    cleanup.push(async (api) => {
      const list = await api.get('auth/me/webhooks')
      for (const h of list?.webhooks || []) if (h.name === name) await api.del(`auth/me/webhooks/${h.id}`)
    })

    await page.goto('/settings?tab=integrations')
    const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('settings.integrations.webhooks') }) })
    const addBtn = card.getByRole('button', { name: t('webhooks.add') }).first()
    await addBtn.click()
    const form = dialog(page, t('webhooks.createTitle'))
    const nameField = form.getByLabel(t('settings.integrations.webhookFieldName'))
    await expect(nameField).toBeFocused()
    await nameField.fill(name)
    await form.getByLabel(t('webhooks.fieldUrl')).fill(receiver.url(name))
    // BEKANNTER FEHLER (17-dialogs.spec.js, "Dialoge liegen ueber der Seite"): Der Dialog wird innerhalb der Karte
    // gerendert (.glass-card hat backdrop-filter -> Bezugsrahmen fuer position:fixed); die DynDNS-Karte darunter
    // verdeckt den Speichern-Knopf. Absenden daher per Enter im Formular (funktioniert auch fuer Nutzer).
    await form.getByLabel(t('webhooks.fieldUrl')).press('Enter')

    // Secret nur einmal sichtbar; ESC schliesst nicht, Tab bleibt im Dialog
    const secretDlg = dialog(page, t('webhooks.secretTitle'))
    await expect(secretDlg).toBeVisible()
    await expectFocusInside(secretDlg)
    await page.keyboard.press('Escape')
    await expect(secretDlg).toBeVisible()
    // Fokusfalle: OneTimeSecretModal steht in der a11y-PENDING-Liste (Umstellung durch WS-W2-NACHARBEIT, Welle 3)
    await expectFocusTrapped(page, secretDlg, 6)
    await secretDlg.getByRole('button', { name: t('secretModal.done') }).click()
    await expect(secretDlg).toBeHidden()
    await expect(card.getByText(t('webhooks.created'))).toBeVisible()

    const item = card.locator('li').filter({ hasText: name })
    await expect(item).toContainText(t('webhooks.active'))

    // Test senden -> Erfolg mit HTTP-Code
    await item.getByRole('button', { name: t('webhooks.test') }).click()
    await expect(item.getByText(pattern('webhooks.testSuccess'))).toBeVisible()

    // Echte Zustellung: Zone und Record des Admins erzeugen Ereignisse
    await adminApi.createZone(zone)
    await adminApi.addRecord('ns1', zone, { name: `hook.${zone}`, type: 'A', contents: ['192.0.2.90'] })
    await expect.poll(async () => (await receiver.deliveries(name)).length, { timeout: 30_000 }).toBeGreaterThan(1)

    const logBtn = item.getByRole('button', { name: t('webhooks.deliveries') }).first()
    await logBtn.click()
    const drawer = dialog(page, t('webhooks.drawerTitle', { name }))
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('button', { name: exact(t('common.close')) }).first()).toBeFocused()
    const delivered = drawer.locator('tbody tr').filter({ hasText: t('webhooks.status.succeeded') })
    await expect(delivered.first()).toBeVisible()
    await expect(drawer.locator('tbody').getByText(zone.replace(/\.$/, ''), { exact: false }).first()).toBeVisible()
    await delivered.first().getByRole('button', { name: t('webhooks.details') }).click()
    await expect(drawer.getByText(t('webhooks.responseExcerpt')).first()).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    await expect(logBtn).toBeFocused()
  })

  test('Unlesbare Ziel-URL: Badge, Test gesperrt, Bearbeiten verlangt neue URL', async ({ page, adminApi }) => {
    const name = unique('ui-hook-bad')
    const created = await adminApi.post('auth/me/webhooks', { name, url: receiver.url(name), events: ['*'] })
    const id = created.webhook.id
    cleanup.push((api) => api.del(`auth/me/webhooks/${id}`))
    // Chiffretext, der sich nicht entschluesseln laesst (wie nach einem Schluesselverlust)
    await db('UPDATE webhooks SET url = ? WHERE id = ?', ['enc:v1:dWktc21va2U6a2FwdXR0', id])

    await page.goto('/settings?tab=integrations')
    const card = page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: t('settings.integrations.webhooks') }) })
    const item = card.locator('li').filter({ hasText: name })
    await expect(item.getByText(t('webhooks.urlUnreadableBadge'), { exact: true })).toBeVisible()
    await expect(item.getByText(t('webhooks.urlUnreadable'))).toBeVisible()
    await expect(item.getByRole('button', { name: t('webhooks.test') })).toBeDisabled()

    await item.getByRole('button', { name: t('webhooks.edit') }).click()
    const form = dialog(page, t('webhooks.editTitle'))
    await expect(form.getByText(t('webhooks.urlUnreadableEdit'))).toBeVisible()
    const url = form.getByLabel(t('webhooks.fieldUrl'))
    await expect(url).toHaveValue('')
    // Ohne neue URL wird nicht gespeichert (Fehler im Dialog), mit neuer URL ist der Webhook wieder lesbar
    await url.press('Enter')
    await expect(form).toBeVisible()
    await expect(form.getByRole('alert').first()).toBeVisible()
    await url.fill(receiver.url(name))
    await url.press('Enter') // Speichern-Knopf ggf. verdeckt (UI-SMOKE-1)
    await expect(form).toBeHidden()
    await expect(item.getByText(t('webhooks.urlUnreadableBadge'), { exact: true })).toHaveCount(0)
    await expect(item.getByRole('button', { name: t('webhooks.test') })).toBeEnabled()
  })
})
