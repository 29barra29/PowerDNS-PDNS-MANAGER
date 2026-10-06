// Seed-Daten und Pruefungen ueber die Panel-API (gleiche Endpunkte wie scripts/e2e/checks, aber aus Node).
// Jede Instanz ist eine eingeloggte Browser-Sitzung (Cookie), damit auch Endpunkte gehen, die eine Sitzung
// verlangen (Benutzer-, Token-, Webhook- und Einstellungsverwaltung).
const crypto = require('node:crypto')
const { request } = require('@playwright/test')
const { BASE_URL, ADMIN_USER, ADMIN_PASSWORD, RECEIVER_URL, SERVERS } = require('./env')
const { totp } = require('./totp')

const API = '/api/v1'

function unique(prefix = 'ui') {
  return `${prefix}-${crypto.randomBytes(4).toString('hex')}`
}

/** Eindeutiger Zonenname mit Punkt am Ende, z. B. ui-zone-1a2b3c4d.e2e.test. */
function uniqueZone(prefix = 'ui') {
  return `${unique(prefix)}.e2e.test.`
}

/** Starkes Zufallspasswort, das die Passwortregeln des Panels erfuellt. */
function strongPassword() {
  return `Ui-${crypto.randomBytes(9).toString('base64url')}-9a`
}

const enc = (v) => encodeURIComponent(String(v))

class ApiError extends Error {
  constructor(method, path, status, body) {
    super(`${method} ${path} -> ${status}: ${typeof body === 'string' ? body : JSON.stringify(body)}`.slice(0, 800))
    this.status = status
    this.body = body
  }
}

class PanelApi {
  constructor(ctx, username, baseURL) {
    this.ctx = ctx
    this.username = username
    this.baseURL = baseURL
  }

  /** Login per Passwort (+ TOTP, falls das Konto 2FA hat). */
  static async login(username, password, { totpSecret, baseURL = BASE_URL } = {}) {
    const ctx = await request.newContext({ baseURL })
    let res = await ctx.post(`${API}/auth/login`, { form: { username, password } })
    let body = await res.json().catch(() => null)
    if (res.ok() && body?.need_two_factor) {
      if (!totpSecret) throw new Error(`Login ${username}: 2FA verlangt, aber kein TOTP-Geheimnis bekannt`)
      res = await ctx.post(`${API}/auth/login/2fa`, {
        data: { two_factor_token: body.two_factor_token, totp_code: totp(totpSecret) },
      })
      body = await res.json().catch(() => null)
    }
    if (!res.ok()) {
      await ctx.dispose()
      throw new ApiError('POST', 'auth/login', res.status(), body)
    }
    const api = new PanelApi(ctx, username, baseURL)
    api.user = body?.user || null
    return api
  }

  static admin() {
    if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD fehlt (scripts/e2e/ui/run.sh setzt es)')
    return PanelApi.login(ADMIN_USER, ADMIN_PASSWORD)
  }

  /**
   * Aufruf relativ zu /api/v1 (Pfad ohne fuehrenden "/") oder absolut ("/health").
   * expect: erlaubter Status (Zahl oder Liste); ohne expect muss die Antwort 2xx sein.
   */
  async call(method, path, { data, form, expect, headers } = {}) {
    const url = path.startsWith('/') ? path : `${API}/${path}`
    const opts = { method, headers }
    if (data !== undefined) opts.data = data
    if (form !== undefined) opts.form = form
    const res = await this.ctx.fetch(url, opts)
    const text = await res.text()
    let body = text
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      /* Text-Antwort */
    }
    const allowed = expect === undefined ? null : [].concat(expect)
    const ok = allowed ? allowed.includes(res.status()) : res.ok()
    if (!ok) throw new ApiError(method, path, res.status(), body)
    return body
  }

  get(path, opts) { return this.call('GET', path, opts) }
  post(path, data, opts = {}) { return this.call('POST', path, { ...opts, data }) }
  put(path, data, opts = {}) { return this.call('PUT', path, { ...opts, data }) }
  del(path, data, opts = {}) { return this.call('DELETE', path, { ...opts, data }) }

  /** Cookies dieser Sitzung als Playwright-storageState (fuer browser.newContext). */
  storageState() { return this.ctx.storageState() }

  async dispose() { await this.ctx.dispose().catch(() => {}) }

  // ----- Zonen und Records -----------------------------------------------------------------
  /** Zone auf allen schreibbaren Servern anlegen (Fan-out wie im UI). */
  async createZone(name, { kind = 'Native', nameservers = ['ns1.e2e.test.', 'ns2.e2e.test.'], ...extra } = {}) {
    return this.post('zones', { name, kind, nameservers, ...extra })
  }

  /** Zone auf allen Servern entfernen; fehlende Zonen sind kein Fehler. */
  async deleteZone(name, servers = SERVERS) {
    for (const srv of servers) {
      await this.call('DELETE', `zones/${enc(srv)}/${enc(name)}`, { expect: [200, 204, 404, 409, 422, 502, 503] })
        .catch(() => {})
    }
  }

  addRecord(server, zone, { name, type, ttl = 300, contents }) {
    return this.post(`records/${enc(server)}/${enc(zone)}`, {
      name, type, ttl, records: [].concat(contents).map((content) => ({ content })),
    })
  }

  async records(server, zone) {
    const data = await this.get(`records/${enc(server)}/${enc(zone)}`)
    return data?.records || []
  }

  async rrsetContents(server, zone, name, type) {
    const recs = await this.records(server, zone)
    return recs.filter((r) => r.name === name && r.type === type).map((r) => r.content).sort()
  }

  // ----- Benutzer ------------------------------------------------------------------------------
  async createUser({ username = unique('uiuser'), password = strongPassword(), role = 'user', ...extra } = {}) {
    const res = await this.post('auth/users', {
      username, password, role, email: `${username}@e2e.test`, display_name: username, ...extra,
    })
    const id = res?.id ?? res?.user?.id
    if (!id) throw new Error(`Benutzer angelegt, aber keine ID: ${JSON.stringify(res)}`)
    return { id, username, password, role }
  }

  deleteUser(id) {
    return this.call('DELETE', `auth/users/${id}`, { expect: [200, 204, 404] }).catch(() => {})
  }

  /** Zonenrechte setzen (ersetzt alle): { 'zone.': 'read'|'manage' } */
  setUserZones(id, perms) {
    return this.put(`auth/users/${id}/zones`, { zones: Object.keys(perms), zone_permissions: perms })
  }

  async findUser(username) {
    const data = await this.get('auth/users')
    const list = Array.isArray(data) ? data : data?.users || []
    return list.find((u) => u.username === username) || null
  }

  /** 2FA fuer die eingeloggte Sitzung einschalten; liefert das Geheimnis. */
  async enableTotp() {
    const begin = await this.post('auth/me/totp/begin', {})
    const secret = begin?.secret || begin?.totp_secret
    if (!secret) throw new Error(`totp/begin ohne Geheimnis: ${JSON.stringify(begin)}`)
    await this.post('auth/me/totp/enable', { code: totp(secret) })
    return secret
  }
}

// ----- Webhook-Empfaenger (scripts/e2e/webhook-receiver.py) -----------------------------------
const receiver = {
  url(name, params = {}) {
    const q = new URLSearchParams(params).toString()
    return `${RECEIVER_URL}/hook/${name}${q ? `?${q}` : ''}`
  },
  async deliveries(name) {
    const ctx = await request.newContext({ baseURL: RECEIVER_URL })
    try {
      const res = await ctx.get(`/deliveries${name ? `?path=${enc(`/hook/${name}`)}` : ''}`)
      const body = await res.json()
      return Array.isArray(body) ? body : body?.deliveries || []
    } finally {
      await ctx.dispose()
    }
  },
}

module.exports = { PanelApi, ApiError, receiver, unique, uniqueZone, strongPassword, enc }
