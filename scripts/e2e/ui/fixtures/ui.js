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

/** Modal ohne role="dialog" (aeltere Dialoge: Karte .glass-card mit Ueberschrift). */
function modal(page, title) {
  const re = title instanceof RegExp ? title : exact(title)
  return page.locator('.glass-card').filter({ has: page.getByRole('heading', { name: re }) }).last()
}

/** Tabellenzeile, die einen Text enthaelt (z. B. einen Record-Wert). */
function rowWith(scope, text) {
  return scope.locator('tr').filter({ hasText: text })
}

/** Naechsten window.confirm() annehmen und seinen Text zurueckgeben (Promise). */
function acceptNextConfirm(page) {
  return new Promise((resolve) => {
    page.once('dialog', async (d) => {
      const msg = d.message()
      await d.accept()
      resolve(msg)
    })
  })
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

/**
 * Der Dialog liegt sichtbar ueber der Seite: Jeder Knopf im Dialog ist nach dem Scrollen tatsaechlich klickbar
 * (elementFromPoint trifft den Dialog, nicht eine andere Karte). Faengt Dialoge, die innerhalb eines Elements mit
 * backdrop-filter/transform gerendert werden (dort wird position:fixed relativ zum Element statt zum Fenster).
 */
async function expectDialogOnTop(dialogLocator) {
  const covered = await dialogLocator.evaluate((dlg) => {
    const out = []
    // Abdeckung (Overlay mit position:fixed) muss das ganze Fenster bedecken
    // (gesucht ab dem Elternelement; ist nur das Dialog-Element selbst fixed, z. B. ein Drawer, entfaellt die Pruefung)
    let overlay = dlg.parentElement
    while (overlay && overlay !== document.body && getComputedStyle(overlay).position !== 'fixed') overlay = overlay.parentElement
    if (overlay && overlay !== document.body) {
      const o = overlay.getBoundingClientRect()
      if (o.left > 1 || o.top > 1 || o.right < window.innerWidth - 1 || o.bottom < window.innerHeight - 1) {
        out.push(`Abdeckung nur ${Math.round(o.width)}x${Math.round(o.height)} statt ${window.innerWidth}x${window.innerHeight} (Bezugsrahmen ist nicht das Fenster)`)
      }
    }
    for (const btn of dlg.querySelectorAll('button')) {
      const label = (btn.getAttribute('aria-label') || btn.textContent || '').trim().slice(0, 60) || '?'
      btn.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      const r = btn.getBoundingClientRect()
      if (!r.width || !r.height) continue
      const x = r.left + r.width / 2
      const y = r.top + r.height / 2
      if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
        out.push(`${label} (ausserhalb des Fensters)`)
        continue
      }
      const hit = document.elementFromPoint(x, y)
      if (!hit || !dlg.contains(hit)) out.push(`${label} (verdeckt von ${hit ? hit.tagName.toLowerCase() + '.' + String(hit.className).split(' ')[0] : 'nichts'})`)
    }
    return out
  })
  expect(covered, 'Knoepfe im Dialog, die nicht klickbar sind').toEqual([])
}

/** Text des aktuell fokussierten Elements (fuer Fokus-Rueckgabe-Pruefungen). */
function activeElementText(page) {
  return page.evaluate(() => {
    const el = document.activeElement
    return el ? (el.getAttribute('aria-label') || el.textContent || '').trim() : ''
  })
}

module.exports = {
  field, checkboxByLabel, zonePath, loginViaUi, expectLoggedIn, dialog, navLink, modal, rowWith, acceptNextConfirm,
  expectFocusInside, expectFocusTrapped, activeElementText, expectDialogOnTop,
}
