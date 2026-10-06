// Kleine UI-Helfer (Selektoren ueber sichtbare Texte aus den Sprachdateien, keine festen Wartezeiten).
const { expect } = require('@playwright/test')
const { t, exact, escapeRe } = require('./i18n')

/**
 * Eingabefeld zu einer sichtbaren Beschriftung. Viele Formulare im Panel haben <label> ohne htmlFor
 * mit dem Feld als naechstem Geschwister (oder darin verschachtelt); getByLabel greift dort nicht.
 */
function field(scope, label, { nth = 0 } = {}) {
  const re = label instanceof RegExp ? label : new RegExp(`^\\s*${escapeRe(label)}\\s*\\*?\\s*$`)
  const lbl = scope.locator('label').filter({ hasText: re }).nth(nth)
  return lbl
    .locator('xpath=(following-sibling::*[1]/descendant-or-self::*[self::input or self::select or self::textarea]'
      + ' | descendant::*[self::input or self::select or self::textarea])[1]')
}

/** Checkbox in einem <label> mit Text (Checkbox im Label verschachtelt). */
function checkboxByLabel(scope, text) {
  const re = text instanceof RegExp ? text : new RegExp(escapeRe(text))
  return scope.locator('label').filter({ hasText: re }).locator('input[type="checkbox"]').first()
}

function zonePath(zone, server = 'ns1') {
  return `/zones/${encodeURIComponent(server)}/${encodeURIComponent(zone)}`
}

/** Anmeldung ueber das Formular der Login-Seite (ohne 2FA). */
async function loginViaUi(page, username, password) {
  await page.goto('/login')
  const form = page.locator('form').first()
  await form.locator('input[type="text"]').first().fill(username)
  await form.locator('input[type="password"]').first().fill(password)
  await form.getByRole('button', { name: exact(t('login.submit')) }).click()
}

/** Link der Seitenleiste (Navigation) per i18n-Key, z. B. navLink(page, 'layout.myZones'). */
function navLink(page, key) {
  return page.locator('aside nav').getByRole('link', { name: t(key), exact: true })
}

/** Seitenleiste vorhanden = Layout geladen (eingeloggt). */
async function expectLoggedIn(page) {
  await expect(page.getByRole('button', { name: new RegExp(escapeRe(t('layout.logout')), 'i') })).toBeVisible()
}

/** Offener Dialog mit role="dialog" (oberster), optional per Titeltext. */
function dialog(page, title) {
  const all = page.locator('[role="dialog"]')
  if (!title) return all.last()
  const re = title instanceof RegExp ? title : new RegExp(escapeRe(title))
  return all.filter({ hasText: re }).last()
}

/** Fokus liegt innerhalb des Locators. */
async function expectFocusInside(locator) {
  await expect.poll(async () => locator.evaluate((el) => el.contains(document.activeElement)), {
    message: 'Fokus liegt im Dialog',
  }).toBe(true)
}

/** Tab mehrfach druecken; der Fokus darf den Dialog nie verlassen (Fokusfalle). */
async function expectFocusTrapped(page, locator, presses = 12) {
  for (let i = 0; i < presses; i += 1) {
    await page.keyboard.press(i % 3 === 2 ? 'Shift+Tab' : 'Tab')
    expect(await locator.evaluate((el) => el.contains(document.activeElement)), `Fokus nach Tab ${i + 1} im Dialog`).toBe(true)
  }
}

/** Text des aktuell fokussierten Elements (fuer Fokus-Rueckgabe-Pruefungen). */
function activeElementText(page) {
  return page.evaluate(() => {
    const el = document.activeElement
    return el ? (el.getAttribute('aria-label') || el.textContent || '').trim() : ''
  })
}

module.exports = {
  field, checkboxByLabel, zonePath, loginViaUi, expectLoggedIn, dialog, navLink,
  expectFocusInside, expectFocusTrapped, activeElementText,
}
