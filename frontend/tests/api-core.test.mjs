// Kernverhalten von src/api.js (W0-INT-FE1a, Plan B.14): request(method, path, data, { signal }),
// AbortError, 401-Ausnahmeliste, 403-Hooks (Passwortwechsel, Step-up), Fehlertexte, API-Modul-Mischung.
//
// api.js nutzt Vite-Features (import.meta.glob, i18n mit Lazy-Locales). Fuer node --test wird eine Kopie
// erzeugt, in der genau diese Stellen ersetzt sind (i18n-Stub, Modul-Map aus globalThis). Aendert sich
// eine dieser Stellen in api.js, schlaegt der Test mit einer klaren Meldung fehl.

import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')

const I18N_IMPORT = "import i18n from './i18n';"
const GLOB = "import.meta.glob('./api/*.js', { eager: true })"
const DEV = 'import.meta.env?.DEV'

const I18N_STUB = `const i18n = {
    exists: (k) => typeof k === 'string' && k.startsWith('apiErrors.'),
    t: (k, o) => (o && o.message !== undefined ? k + '|' + o.message : k),
};`

// window/fetch-Attrappen (vor dem Import, weil api.js window erst zur Laufzeit nutzt)
const win = new EventTarget()
win.location = { pathname: '/', href: '/' }
globalThis.window = win

let fetchImpl = null
globalThis.fetch = (...args) => fetchImpl(...args)

function jsonResponse(status, body, headers = {}) {
    return new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json', ...headers },
    })
}

async function loadApi(modules = {}) {
    let src = fs.readFileSync(path.join(SRC, 'api.js'), 'utf-8')
    for (const needle of [I18N_IMPORT, GLOB, DEV]) {
        assert.ok(src.includes(needle), `api.js enthaelt "${needle}" nicht mehr – Test anpassen`)
    }
    src = src.replace(I18N_IMPORT, I18N_STUB).replace(GLOB, 'globalThis.__API_MODULES__').replace(DEV, 'true')
    globalThis.__API_MODULES__ = modules
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'api-core-'))
    const file = path.join(dir, 'api.mjs')
    fs.writeFileSync(file, src)
    try {
        return await import(pathToFileURL(file).href)
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
}

const mod = await loadApi()
const api = mod.default

beforeEach(() => {
    win.location = { pathname: '/zones', href: '/zones' }
    api.clearUser()
    fetchImpl = null
})

test('benannte Exporte vorhanden', () => {
    assert.equal(typeof mod.extractErrorMessage, 'function')
    assert.equal(typeof mod.extractErrorCode, 'function')
    assert.equal(mod.PASSWORD_CHANGE_EVENT, 'pdns:password-change-required')
    assert.equal(mod.STEP_UP_EVENT, 'pdns:step-up-required')
    assert.deepEqual([...mod.STEP_UP_CODES], ['stepup_required', 'reauth_required'])
    for (const p of ['/login', '/setup', '/register', '/forgot-password', '/reset-password']) {
        assert.ok(mod.AUTH_REDIRECT_EXEMPT_PATHS.includes(p), p)
    }
})

test('extractErrorMessage: Formen und Fallback', () => {
    const { extractErrorMessage: m } = mod
    assert.equal(m({ detail: 'kaputt' }, 'Bad Request', 400), 'kaputt')
    assert.equal(m({ detail: { message: 'strukturiert', code: 'x' } }, '', 403), 'strukturiert')
    assert.equal(m({ detail: [{ loc: ['body', 'name'], msg: 'zu kurz' }, { loc: ['body', 'ttl'], msg: 'zu klein' }] }, '', 422),
        'name: zu kurz · ttl: zu klein')
    assert.equal(m({ error: 'PowerDNS API Error', server: 'ns1', detail: 'nope' }, '', 502), 'ns1: nope')
    assert.equal(m(null, 'Bad Gateway', 502), 'Bad Gateway')
    assert.equal(m(null, '', 502), 'HTTP 502')
    assert.equal(m(null, '', 502, 'Anmeldung fehlgeschlagen'), 'Anmeldung fehlgeschlagen (HTTP 502)')
    // HTML-Fehlerseite eines Proxys wird nicht als Meldung angezeigt
    assert.equal(m('<html><body>502</body></html>', 'Bad Gateway', 502), 'Bad Gateway')
    assert.equal(m('  reiner Text  ', '', 500), 'reiner Text')
})

test('extractErrorCode', () => {
    const { extractErrorCode: c } = mod
    assert.equal(c({ detail: { code: 'stepup_required', message: 'x' } }), 'stepup_required')
    assert.equal(c({ code: 'reauth_required' }), 'reauth_required')
    assert.equal(c({ detail: 'stepup_required' }), 'stepup_required')
    assert.equal(c({ detail: 'Ein normaler Satz.' }), null)
    assert.equal(c(null), null)
})

test('request: JSON-Body, Header, Credentials, signal; GET ohne Body; 204 -> null', async () => {
    const calls = []
    fetchImpl = async (url, init) => {
        calls.push({ url, init })
        return init.method === 'DELETE' ? new Response(null, { status: 204 }) : jsonResponse(200, { ok: true })
    }
    const ctrl = new AbortController()
    assert.deepEqual(await api.request('POST', '/x', { a: 1 }, { signal: ctrl.signal }), { ok: true })
    assert.equal(calls[0].url, '/api/v1/x')
    assert.equal(calls[0].init.body, '{"a":1}')
    assert.equal(calls[0].init.credentials, 'include')
    assert.equal(calls[0].init.signal, ctrl.signal)
    assert.equal(calls[0].init.headers['Content-Type'], 'application/json')
    await api.request('GET', '/y', { ignored: true })
    assert.equal(calls[1].init.body, undefined)
    assert.equal(await api.request('DELETE', '/z'), null)
})

test('request: AbortError wird unveraendert durchgereicht', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError')
    fetchImpl = async () => { throw abort }
    await assert.rejects(api.request('GET', '/x'), (err) => err === abort)
})

test('request: Netzwerkfehler -> apiErrors.serverUnreachable mit Meldung', async () => {
    fetchImpl = async () => { throw new TypeError('Failed to fetch') }
    await assert.rejects(api.request('GET', '/x'), (err) => {
        assert.equal(err.message, 'apiErrors.serverUnreachable|Failed to fetch')
        assert.equal(err.status, 0)
        assert.equal(err.network, true)
        return true
    })
})

test('401: Cache leeren und auf /login springen, ausser auf Seiten der Ausnahmeliste', async () => {
    fetchImpl = async () => jsonResponse(401, { detail: 'Not authenticated' })
    api.setUser({ username: 'a' })
    await assert.rejects(api.request('GET', '/auth/me'), (err) => err.status === 401 && err.message === 'apiErrors.sessionExpired')
    assert.equal(api.getUser(), null)
    assert.equal(win.location.href, '/login')

    for (const p of ['/login', '/reset-password', '/setup']) {
        win.location = { pathname: p, href: p }
        await assert.rejects(api.request('GET', '/auth/me'), (err) => err.status === 401)
        assert.equal(win.location.href, p, `kein Sprung auf ${p}`)
    }
})

test('401 mit authRedirect:false: kein Sprung, Backend-Meldung bleibt', async () => {
    fetchImpl = async () => jsonResponse(401, { detail: 'Benutzername oder Passwort falsch' })
    await assert.rejects(api.request('POST', '/x', {}, { authRedirect: false }), (err) => err.status === 401 && err.message === 'Benutzername oder Passwort falsch')
    assert.equal(win.location.href, '/zones')
})

test('login: 401 fuehrt nicht zum Seitensprung, Fallback-Text ohne Meldung', async () => {
    win.location = { pathname: '/login', href: '/login' }
    fetchImpl = async () => new Response('', { status: 401, headers: { 'content-type': 'text/plain' } })
    await assert.rejects(api.login('u', 'p'), (err) => err.status === 401 && err.message === 'apiErrors.loginFailed (HTTP 401)')
    assert.equal(win.location.href, '/login')
})

test('403 + X-Password-Change-Required: Event und Flag im Cache', async () => {
    let fired = 0
    const on = () => { fired++ }
    win.addEventListener(mod.PASSWORD_CHANGE_EVENT, on)
    api.setUser({ username: 'a', must_change_password: false })
    fetchImpl = async () => jsonResponse(403, { detail: 'Passwortänderung erforderlich' }, { 'X-Password-Change-Required': '1' })
    await assert.rejects(api.request('GET', '/zones/ns1'), (err) => err.status === 403)
    win.removeEventListener(mod.PASSWORD_CHANGE_EVENT, on)
    assert.equal(fired, 1)
    assert.equal(api.getUser().must_change_password, true)
})

test('403 stepup_required/reauth_required: STEP_UP_EVENT mit Details; stepup_failed nicht', async () => {
    const seen = []
    const on = (e) => seen.push(e.detail)
    win.addEventListener(mod.STEP_UP_EVENT, on)
    fetchImpl = async () => jsonResponse(403, { detail: { code: 'stepup_required', message: 'Bitte Passwort bestaetigen' } })
    await assert.rejects(api.request('PUT', '/settings/sso', { a: 1 }), (err) => err.code === 'stepup_required' && err.message === 'Bitte Passwort bestaetigen')
    fetchImpl = async () => jsonResponse(403, { detail: 'Anmeldung zu alt' }, { 'X-Step-Up-Required': 'reauth_required' })
    await assert.rejects(api.request('POST', '/auth/users/1/convert-to-local', {}), (err) => err.code === 'reauth_required')
    fetchImpl = async () => jsonResponse(403, { detail: { code: 'stepup_failed', message: 'falsch' } })
    await assert.rejects(api.request('PUT', '/settings/sso', {}), (err) => err.code === 'stepup_failed')
    win.removeEventListener(mod.STEP_UP_EVENT, on)
    assert.deepEqual(seen, [
        { code: 'stepup_required', method: 'PUT', path: '/settings/sso', message: 'Bitte Passwort bestaetigen' },
        { code: 'reauth_required', method: 'POST', path: '/auth/users/1/convert-to-local', message: 'Anmeldung zu alt' },
    ])
})

test('search: kodiert Server und Query, max_results, signal', async () => {
    let seen
    fetchImpl = async (url, init) => { seen = { url, init }; return jsonResponse(200, { results: [] }) }
    const ctrl = new AbortController()
    await api.search('ns 1', 'a&b', { signal: ctrl.signal, maxResults: 50 })
    assert.equal(seen.url, '/api/v1/search/ns%201?q=a%26b&max_results=50')
    assert.equal(seen.init.signal, ctrl.signal)
})

test('API-Module werden sortiert in den Prototyp gemischt (this = Client)', async () => {
    const warnings = []
    const origWarn = console.warn
    console.warn = (...a) => warnings.push(a.join(' '))
    let m2
    try {
        m2 = await loadApi({
            './api/b.js': { default: { fooB() { return this.request('GET', '/b') } } },
            './api/a.js': { default: { fooA() { return 'a' } } },
            './api/f7-fe.js': { overrides: ['getAuditLog'], default: { getAuditLog() { return 'neu' } } },
            './api/x.js': { default: { listZones() { return 'heimlich' } } },
        })
    } finally {
        console.warn = origWarn
    }
    const c = m2.default
    assert.equal(c.fooA(), 'a')
    assert.equal(c.getAuditLog(), 'neu')
    fetchImpl = async (url) => jsonResponse(200, { url })
    assert.deepEqual(await c.fooB(), { url: '/api/v1/b' })
    // undeklarierte Ueberschreibung -> Warnung (der statische Test api-modules.test.mjs bricht dafuer ab)
    assert.equal(warnings.length, 1)
    assert.match(warnings[0], /x\.js.*listZones/)
})
