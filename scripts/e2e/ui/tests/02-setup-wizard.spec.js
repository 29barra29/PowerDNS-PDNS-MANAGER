// Einrichtungsassistent auf einer frischen Datenbank (zweite Backend-Instanz "setup-backend", run.sh legt die
// Datenbank je Lauf neu an). Quellen: F8b Nr. 21 (J01 Benutzername, SMTP aus dem Assistenten), FE1a.
const { test, expect, EMPTY_STATE } = require('../fixtures/test')
const { SETUP_URL } = require('../fixtures/env')
const { strongPassword } = require('../fixtures/api')
const { t, exact } = require('../fixtures/i18n')
const { field, checkboxByLabel, expectLoggedIn } = require('../fixtures/ui')

test.describe('Einrichtungsassistent (frische Datenbank)', () => {
  test.use({ storageState: EMPTY_STATE })
  test.skip(!SETUP_URL, 'setup-backend nicht gestartet (run.sh --no-setup)')
  // Nicht wiederholbar: nach dem ersten Versuch existiert der Admin bereits (run.sh legt die DB je Lauf neu an)
  test.describe.configure({ retries: 0 })

  test('Erster Admin mit SMTP-Daten -> Dashboard; SMTP danach in den Einstellungen, Passwort maskiert', async ({ page }) => {
    const password = strongPassword()
    await page.goto(`${SETUP_URL}/`)
    await expect(page).toHaveURL(/\/setup$/)
    await expect(page.getByRole('heading', { name: t('setup.step1Title') })).toBeVisible()

    // Schritt 1: ungueltiger Benutzername wird frueh gemeldet (F8-J01)
    await field(page, t('login.username')).fill('a b')
    await field(page, t('register.email')).fill('setup-admin@example.com')
    await field(page, t('login.password')).fill(password)
    await field(page, t('setup.passwordRepeat')).fill(password)
    await page.getByRole('button', { name: exact(t('setup.next')) }).click()
    await expect(page.getByText(t('setup.usernamePatternHint')).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: t('setup.step1Title') })).toBeVisible()

    await field(page, t('login.username')).fill('setupadmin')
    await field(page, t('register.displayName')).fill('Setup Admin')
    await page.getByRole('button', { name: exact(t('setup.next')) }).click()

    // Schritt 2: E-Mail optional aktivieren
    await expect(page.getByRole('heading', { name: t('setup.step2Title') })).toBeVisible()
    await checkboxByLabel(page, t('setup.enableEmail')).check()
    await field(page, t('setup.smtpServer')).fill('smtp.e2e.test')
    await field(page, t('setup.port')).fill('587')
    await field(page, t('setup.smtpUser')).fill('mailer@example.com')
    await field(page, t('setup.smtpPassword')).fill('smtp-geheim-123')
    await field(page, t('setup.senderEmail')).fill('noreply@example.com')
    await page.getByRole('button', { name: exact(t('setup.next')) }).click()

    // Schritt 3: Zusammenfassung und Abschluss
    await expect(page.getByRole('heading', { name: t('setup.step3Title') })).toBeVisible()
    await expect(page.getByText('setupadmin')).toBeVisible()
    await expect(page.getByText('smtp.e2e.test:587')).toBeVisible()
    await page.getByRole('button', { name: exact(t('setup.completeSetup')) }).click()
    await expect(page.getByRole('heading', { name: t('setup.step4Title') })).toBeVisible()

    // Weiterleitung aufs Dashboard (angemeldet als der neue Admin)
    await expect(page).toHaveURL(`${SETUP_URL}/`, { timeout: 20_000 })
    await expectLoggedIn(page)
    await expect(page.getByText('Setup Admin').first()).toBeVisible()

    // SMTP aus dem Assistenten ist gespeichert; das Passwort wird nie zurueckgegeben (nur Platzhalter)
    await page.goto(`${SETUP_URL}/settings?tab=smtp`)
    await expect(field(page, t('settings.smtpServer'))).toHaveValue('smtp.e2e.test')
    const pw = page.locator('input[type="password"]').first()
    await expect(pw).toHaveValue('')
    await expect(pw).toHaveAttribute('placeholder', t('settings.smtpPasswordKeepPlaceholder'))

    // Assistent ist danach gesperrt
    await page.goto(`${SETUP_URL}/setup`)
    await expect(page).not.toHaveURL(/\/setup$/)
  })
})
