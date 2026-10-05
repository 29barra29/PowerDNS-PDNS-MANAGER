// Welle-2-Integrationstest WS-F10-APP-FE <-> WS-F10-APP-BE / WS-F10-SVC (Plan Regel 3, F.2): die SSO-Oberflaeche
// passt zum API-Vertrag. Liest die Backend-Quellen statisch (kein Python noetig):
//   backend/app/services/sso_oidc.py, sso_provisioning.py, sso_settings.py   Fehlercodes, SENSITIVE_FIELDS (WS-F10-SVC)
//   backend/app/routers/sso.py, settings_sso.py, auth.py, core/auth.py        Pfade, sso_error-Codes, Step-up (WS-F10-APP-BE)
// Vor dem Merge von WS-F10-APP-BE fehlen die Router -> diese Teile werden mit Grund uebersprungen.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { SAFE_UNIQUE_ID_ATTRS, SENSITIVE_FIELDS, SSO_ERROR_KEYS } from '../../src/components/sso/ssoModel.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..', '..')
const BACKEND = path.resolve(FRONTEND, '..', 'backend', 'app')
const read = (...p) => fs.readFileSync(path.join(BACKEND, ...p), 'utf-8')
const exists = (...p) => fs.existsSync(path.join(BACKEND, ...p))

const ROUTERS = [['routers', 'sso.py'], ['routers', 'settings_sso.py']]
const missingRouters = ROUTERS.filter((p) => !exists(...p)).map((p) => p.join('/'))
const SKIP_BE = missingRouters.length ? `WS-F10-APP-BE noch nicht gemergt (fehlt: ${missingRouters.join(', ')})` : false

function codes(src, re) {
    return [...new Set([...src.matchAll(re)].map((m) => m[1]))]
}

test('Fehlercodes der SSO-Dienste haben einen Text (login.ssoError.*)', () => {
    const oidc = codes(read('services', 'sso_oidc.py'), /OidcError\(\s*"([a-z_]+)"/g)
    const prov = codes(read('services', 'sso_provisioning.py'), /ProvisioningError\(\s*"([a-z_]+)"/g)
    assert.ok(oidc.length >= 3 && prov.length >= 3, 'keine Codes gefunden – Muster pruefen')
    for (const c of [...oidc, ...prov]) assert.ok(c in SSO_ERROR_KEYS, `Code ${c} fehlt in SSO_ERROR_KEYS`)
})

test('SENSITIVE_FIELDS und SAFE_UNIQUE_ID_ATTRS spiegeln das Backend', () => {
    const src = read('services', 'sso_settings.py')
    const block = src.match(/SENSITIVE_FIELDS[^=]*=\s*\{([\s\S]*?)\n\}/)
    assert.ok(block, 'SENSITIVE_FIELDS nicht gefunden')
    for (const section of ['general', 'oidc', 'ldap']) {
        const m = block[1].match(new RegExp(`"${section}":\\s*frozenset\\(\\{([\\s\\S]*?)\\}\\)`))
        assert.ok(m, `Abschnitt ${section} fehlt`)
        const backend = codes(m[1], /"([a-z_]+)"/g).sort()
        assert.deepEqual([...SENSITIVE_FIELDS[section]].sort(), backend, `SENSITIVE_FIELDS.${section}`)
    }
    const schema = read('schemas', 'sso.py').match(/SAFE_UNIQUE_ID_ATTRS\s*=\s*\(([^)]*)\)/)
    assert.ok(schema)
    assert.deepEqual([...SAFE_UNIQUE_ID_ATTRS].sort(), codes(schema[1], /"([A-Za-z]+)"/g).sort())
})

test('Router-Pfade, die api/f10-app-fe.js aufruft, existieren', { skip: SKIP_BE }, () => {
    const sso = read('routers', 'sso.py')
    const settingsSso = read('routers', 'settings_sso.py')
    const auth = read('routers', 'auth.py')
    for (const p of ['/sso/providers', '/oidc/start', '/oidc/callback', '/me/sso/oidc/link', '/me/sso/ldap/link']) {
        assert.ok(sso.includes(`"${p}"`), `routers/sso.py: ${p} fehlt`)
    }
    for (const p of ['"/sso"', '"/sso/test"']) assert.ok(settingsSso.includes(p), `routers/settings_sso.py: ${p} fehlt`)
    assert.ok(/convert-to-local/.test(auth), 'routers/auth.py: convert-to-local fehlt')
})

test('sso_error-Codes der Router haben einen Text', { skip: SKIP_BE }, () => {
    const src = read('routers', 'sso.py')
    const found = codes(src, /sso_error=([a-z_]+)/g)
        .concat(codes(src, /_fail\(\s*"([a-z_]+)"/g))
        .concat(codes(src, /_error_redirect\([^)]*?"([a-z_]+)"/g))
    for (const c of found) assert.ok(c in SSO_ERROR_KEYS, `sso_error=${c} fehlt in SSO_ERROR_KEYS`)
    assert.ok(src.includes('totp_unreadable'), 'OIDC-Callback meldet totp_unreadable nicht [S4]')
})

test('Step-up: Codes und Feld step_up wie im Frontend [S8]', { skip: SKIP_BE }, () => {
    const core = read('core', 'auth.py')
    for (const c of ['stepup_required', 'stepup_failed', 'reauth_required']) assert.ok(core.includes(c), `core/auth.py: ${c} fehlt`)
    assert.ok(/def verify_step_up/.test(core), 'verify_step_up fehlt')
    assert.ok(/step_up/.test(read('routers', 'settings_sso.py')), 'PUT /settings/sso liest step_up nicht')
    const auth = read('routers', 'auth.py')
    const m = auth.match(/class ConvertToLocalBody[\s\S]*?(?=\n\S)/) || read('schemas', 'sso.py').match(/class ConvertToLocalBody[\s\S]*?(?=\n\S)/)
    if (m) assert.ok(/step_up/.test(m[0]), 'ConvertToLocalBody ohne step_up (Frontend sendet step_up: {current_password, totp_code})')
})

test('Antrag: general.session_max_age in GET /settings/sso (Warnung Sitzungsdauer)', { skip: SKIP_BE }, (t) => {
    const src = read('routers', 'settings_sso.py') + read('services', 'sso_settings.py')
    if (!src.includes('session_max_age')) t.skip('Antrag an WS-F10-APP-BE nicht umgesetzt – UI zeigt nur die allgemeine Empfehlung')
})
