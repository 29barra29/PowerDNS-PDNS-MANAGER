// Externe Aufrufe des Frontends (nur api.github.com: Versionspruefung im Layout, Commits im Reiter "Updates")
// werden im Test beantwortet, damit die Specs nicht vom Internet bzw. GitHub-Ratenlimit abhaengen.
// Jede andere Anfrage ausserhalb des E2E-Netzes wird abgebrochen und protokolliert (z. B. Google Fonts, F8-B04).
const { BASE_URL, SETUP_URL } = require('./env')

const STUB_VERSION = 'v0.0.1' // aelter als jede echte Version -> kein "Update verfuegbar"-Punkt
const STUB_COMMITS = [{
  sha: 'e2e0000000000000000000000000000000000001',
  html_url: 'https://github.com/example/commit/e2e0000',
  commit: { message: 'E2E: Beispiel-Commit', author: { name: 'E2E', date: '2026-01-01T00:00:00Z' } },
}]

function allowedOrigin(url) {
  const internal = [BASE_URL, SETUP_URL].filter(Boolean).map((u) => new URL(u).origin)
  try {
    const u = new URL(url)
    return internal.includes(u.origin) || u.protocol === 'data:' || u.protocol === 'blob:'
  } catch {
    return true
  }
}

function githubStub(route) {
  const path = new URL(route.request().url()).pathname
  if (path.endsWith('/releases/latest')) return route.fulfill({ json: { tag_name: STUB_VERSION, html_url: 'https://github.com/' } })
  if (path.endsWith('/tags')) return route.fulfill({ json: [{ name: STUB_VERSION }] })
  if (path.endsWith('/commits')) return route.fulfill({ json: STUB_COMMITS })
  return route.fulfill({ status: 404, json: { message: 'Not Found' } })
}

/** Routen fuer einen Browser-Kontext setzen; blockierte externe URLs landen in `blocked`. */
async function routeExternal(context, blocked = []) {
  // Ein Handler fuer alles (Playwright fragt spaeter registrierte Routen zuerst; so gibt es keine Reihenfolge-Falle)
  await context.route(/^https?:\/\//, (route) => {
    const url = route.request().url()
    if (allowedOrigin(url)) return route.fallback()
    if (new URL(url).hostname === 'api.github.com') return githubStub(route)
    blocked.push(url)
    return route.abort('blockedbyclient')
  })
}

module.exports = { routeExternal, STUB_COMMITS }
