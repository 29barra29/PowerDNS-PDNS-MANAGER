// F10 §2.8 in der Benutzerverwaltung (WS-F10-APP-FE, Fix-Runde Welle 2): Loesch-Rueckfrage fuer externe Konten
// und Rollen-Tooltip ueber den sso-Block aus GET /auth/users. Reine Helfer aus ssoModel.js plus statische
// Verdrahtungspruefung von UsersPage.jsx (JSX laesst sich unter node --test nicht direkt laden).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { userDeleteConfirmKey, usersSsoContext } from '../src/components/sso/ssoModel.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8')

test('userDeleteConfirmKey: externe Konten (oidc/ldap/unbekannt) bekommen die JIT-Warnung', () => {
    assert.equal(userDeleteConfirmKey({ auth_source: 'oidc' }), 'users.deleteExternalConfirm')
    assert.equal(userDeleteConfirmKey({ auth_source: 'ldap' }), 'users.deleteExternalConfirm')
    assert.equal(userDeleteConfirmKey({ auth_source: 'saml' }), 'users.deleteExternalConfirm')
    assert.equal(userDeleteConfirmKey({ auth_source: 'local' }), 'users.deleteConfirm')
    assert.equal(userDeleteConfirmKey({}), 'users.deleteConfirm')
    assert.equal(userDeleteConfirmKey(null), 'users.deleteConfirm')
})

test('usersSsoContext: uebernimmt Rollen-Modus und JIT, verwirft Unbekanntes', () => {
    const ctx = usersSsoContext({
        users: [],
        sso: { oidc_role_mode: 'sync', ldap_role_mode: 'promote', oidc_jit: true, ldap_jit: false, extra: 'x' },
    })
    assert.deepEqual(ctx, { oidc_role_mode: 'sync', ldap_role_mode: 'promote', oidc_jit: true, ldap_jit: false })
    const odd = usersSsoContext({ sso: { oidc_role_mode: 'SYNC', ldap_role_mode: 42, oidc_jit: 'true' } })
    assert.deepEqual(odd, { oidc_role_mode: 'off', ldap_role_mode: 'off', oidc_jit: false, ldap_jit: false })
    assert.equal(usersSsoContext({ users: [] }), null)
    assert.equal(usersSsoContext({ sso: null }), null)
    assert.equal(usersSsoContext({ sso: ['sync'] }), null)
    assert.equal(usersSsoContext(undefined), null)
})

test('usersSsoContext passt zum Vertrag des Auth-Badges (role_mode sync -> Tooltip)', () => {
    const badge = read('components/users/badges/20-auth-source.badge.jsx')
    assert.match(badge, /context\?\.sso\?\.\[ldap \? 'ldap_role_mode' : 'oidc_role_mode'\] === 'sync'/)
    assert.match(badge, /users\.roleManagedBySso/)
})

test('UsersPage.jsx: sso-Block als Badge-Kontext, Loesch-Rueckfrage ueber userDeleteConfirmKey', () => {
    const page = read('pages/UsersPage.jsx')
    assert.match(page, /import \{[^}]*\buserDeleteConfirmKey\b[^}]*\busersSsoContext\b[^}]*\} from '\.\.\/components\/sso\/ssoModel'/)
    assert.match(page, /setSsoContext\(usersSsoContext\(userData\)\)/)
    assert.match(page, /<UserBadges user=\{u\} context=\{\{ meId: me\?\.id \?\? null, sso: ssoContext \}\} \/>/)
    assert.match(page, /confirm\(t\(userDeleteConfirmKey\(user\), \{ name \}\)\)/)
    assert.match(page, /onClick=\{\(\) => handleDelete\(u\)\}/)
    // Der allgemeine Text darf nicht mehr fest verdrahtet sein
    assert.doesNotMatch(page, /t\('users\.deleteConfirm'/)
})

test('Locales: beide Keys in allen 6 Sprachen vorhanden, deleteExternalConfirm mit {{name}}', () => {
    for (const lang of ['de', 'en', 'bs', 'hr', 'hu', 'sr']) {
        const users = JSON.parse(read(`locales/${lang}.json`)).users
        assert.ok(users?.deleteExternalConfirm?.includes('{{name}}'), `${lang}: deleteExternalConfirm`)
        assert.ok(users?.roleManagedBySso, `${lang}: roleManagedBySso`)
        assert.ok(users?.deleteConfirm, `${lang}: deleteConfirm`)
    }
})
