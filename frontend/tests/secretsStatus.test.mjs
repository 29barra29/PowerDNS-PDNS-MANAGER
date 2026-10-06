// WS-F5-FE (vom Integrator Welle 3 uebernommen):
// reine Auswertungshelfer aus src/api/secrets.js und Vollstaendigkeit der i18n-Keys der F5-FE-Dateien.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import mod, {
    SECRET_FIELD_LABEL_KEYS, secretColumnsForDisplay, secretsBannerKind, secretsBannerSignature, secretsTotals,
    settingTabForField, unreadableCount, unreadableServerNames,
} from '../src/api/secrets.js'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const base = { mode: 'encrypted', health: 'ok', key_fingerprint: 'abcdef012345', issues: [], columns: [], unreadable: [] }

test('secrets: Banner nur bei health error, nennt unlesbare Server sortiert', () => {
    assert.equal(secretsBannerKind(null), null)
    assert.equal(secretsBannerKind(base), null)
    assert.equal(secretsBannerKind({ ...base, health: 'warning' }), null)
    assert.equal(secretsBannerKind({ ...base, health: 'error', mode: 'plaintext_fallback' }), 'plaintext')
    const st = {
        ...base, health: 'error', issues: ['unreadable_values'],
        unreadable: [
            { kind: 'server', field: 'server_configs.api_key', id: 2, name: 'ns2' },
            { kind: 'server', field: 'server_configs.api_key', id: 1, name: 'ns1' },
            { kind: 'setting', field: 'system_settings.smtp_password', name: 'smtp_password' },
        ],
    }
    assert.equal(secretsBannerKind(st), 'unreadable')
    assert.deepEqual(unreadableServerNames(st), ['ns1', 'ns2'])
    assert.equal(unreadableCount(st), 3)
    assert.equal(secretsBannerSignature(st), 'abcdef012345:3:encrypted')
})

test('secrets: Summen und Tabellenzeilen (bekannte Felder mit Nullen, unbekannte angehaengt)', () => {
    const st = { columns: [
        { id: 'server_configs.api_key', encrypted: 2, encrypted_old: 1, plaintext: 1, unreadable: 0, empty: 3 },
        { id: 'system_settings.neu', encrypted: 0, encrypted_old: 0, plaintext: 0, unreadable: 2, empty: 0 },
    ] }
    assert.deepEqual(secretsTotals(st), { encrypted: 3, encryptedOld: 1, plaintext: 1, unreadable: 2, empty: 3, stored: 6 })
    assert.equal(secretsTotals({}).stored, 0)
    const rows = secretColumnsForDisplay(st)
    assert.equal(rows.length, Object.keys(SECRET_FIELD_LABEL_KEYS).length + 1)
    assert.equal(rows[0].id, 'server_configs.api_key')
    assert.equal(rows[0].encrypted, 2)
    assert.equal(rows.at(-1).id, 'system_settings.neu')
    assert.ok(rows.slice(1, -1).every((r) => r.encrypted === 0 && r.empty === 0))
})

test('secrets: Sprungziele und API-Methode', () => {
    assert.equal(settingTabForField('system_settings.smtp_password'), 'smtp')
    assert.equal(settingTabForField('system_settings.captcha_secret_key'), 'security')
    assert.equal(settingTabForField('system_settings.oidc_client_secret'), 'sso')
    assert.equal(settingTabForField('unbekannt'), null)
    assert.equal(typeof mod.getSecretsStatus, 'function')
    assert.equal(typeof mod.testSmtpSettings, 'function')
})

test('secrets: alle i18n-Keys der F5-FE-Dateien existieren in de.json', () => {
    const de = JSON.parse(fs.readFileSync(path.join(SRC, 'locales', 'de.json'), 'utf8'))
    const get = (k) => k.split('.').reduce((o, p) => (o && typeof o === 'object' ? o[p] : undefined), de)
    const files = [
        'components/SecretsStatusCard.jsx', 'components/banners/10-secrets.banner.jsx', 'api/secrets.js',
        'components/settings/tabs/servers.tab.jsx', 'components/settings/tabs/smtp.tab.jsx', 'components/settings/tabs/security.tab.jsx',
    ]
    const missing = []
    for (const f of files) {
        const src = fs.readFileSync(path.join(SRC, f), 'utf8')
        for (const m of src.matchAll(/['"]((?:settings|settingsMore|layout|common)\.[A-Za-z0-9_.]+)['"]/g)) {
            if (typeof get(m[1]) !== 'string') missing.push(`${f}: ${m[1]}`)
        }
    }
    assert.deepEqual(missing, [])
})
