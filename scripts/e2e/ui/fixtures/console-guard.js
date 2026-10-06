// Konsolen-Waechter: sammelt console.error und ungefangene Fehler (pageerror) aller Seiten eines Tests.
//
// Regeln:
// - pageerror (ungefangene Ausnahme) ist immer ein Fehler.
// - console.error der App ist ein Fehler, ausser der Test erlaubt ihn ausdruecklich (guard.allow(/.../)).
// - Netzwerkmeldungen des Browsers "Failed to load resource: ... status of 4xx" sind erwartbar (401 vor dem
//   Login, 403/404/409/422 in Negativ-Faellen) und werden nur protokolliert; 5xx dagegen zaehlen als Fehler.
const NETWORK_4XX = /^Failed to load resource: the server responded with a status of 4\d\d\b/

class ConsoleGuard {
  constructor() {
    this.entries = []
    this.allowed = []
    this._pages = new WeakSet()
  }

  /** Erwartete Meldung fuer diesen Test freigeben (RegExp oder Teilstring). */
  allow(pattern) {
    this.allowed.push(pattern instanceof RegExp ? pattern : new RegExp(String(pattern).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }

  watchContext(context) {
    context.on('page', (p) => this.watchPage(p))
    for (const p of context.pages()) this.watchPage(p)
  }

  watchPage(page) {
    if (this._pages.has(page)) return
    this._pages.add(page)
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return
      const loc = msg.location() || {}
      this.entries.push({ kind: 'console', text: msg.text(), page: page.url(), source: loc.url ? `${loc.url}:${loc.lineNumber}` : '' })
    })
    page.on('pageerror', (err) => {
      this.entries.push({ kind: 'pageerror', text: String(err?.stack || err), page: page.url(), source: '' })
    })
  }

  isProblem(entry) {
    if (entry.kind === 'console' && NETWORK_4XX.test(entry.text)) return false
    return !this.allowed.some((re) => re.test(entry.text))
  }

  problems() {
    return this.entries.filter((e) => this.isProblem(e))
  }
}

module.exports = { ConsoleGuard }
