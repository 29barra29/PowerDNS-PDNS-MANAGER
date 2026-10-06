// Gesperrte DynDNS-Secrets (WS-W3-NACHARBEIT, A2): kein "Aktivieren" fuer gesperrte Tokens, 409 uebersetzt; die
// Karte und der Dialog nutzen die Helfer wirklich (statisch).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { canToggleActive, dyndnsActionError, isSecretRevoked } from '../src/components/dyndns/dyndnsRevoked.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8')

test('isSecretRevoked: nur bei secret_revoked === true', () => {
    assert.equal(isSecretRevoked({ secret_revoked: true }), true)
    assert.equal(isSecretRevoked({ secret_revoked: false }), false)
    assert.equal(isSecretRevoked({}), false)
    assert.equal(isSecretRevoked(null), false)
})

test('canToggleActive: Deaktivieren immer, Aktivieren nicht bei gesperrtem Secret', () => {
    assert.equal(canToggleActive({ is_active: true }), true)
    assert.equal(canToggleActive({ is_active: true, secret_revoked: true }), true)
    assert.equal(canToggleActive({ is_active: false }), true)
    assert.equal(canToggleActive({ is_active: false, secret_revoked: false }), true)
    assert.equal(canToggleActive({ is_active: false, secret_revoked: true }), false)
    assert.equal(canToggleActive(null), false)
})

test('dyndnsActionError: 409 -> uebersetzter Text, sonst Backend-Meldung', () => {
    const t = (key) => `T:${key}`
    const conflict = Object.assign(new Error('Das Secret dieses Tokens wurde ... gesperrt'), { status: 409 })
    assert.equal(dyndnsActionError(conflict, t), 'T:dyndns.secretRevokedActivate')
    const other = Object.assign(new Error('Token nicht gefunden'), { status: 404 })
    assert.equal(dyndnsActionError(other, t), 'Token nicht gefunden')
    assert.equal(dyndnsActionError(null, t), '')
})

test('Karte und Dialog nutzen die Helfer (kein Aktivieren, 409 uebersetzt)', () => {
    const card = read('components/settings/integrations-cards/50-dyndns.card.jsx')
    assert.match(card, /from '\.\.\/\.\.\/dyndns\/dyndnsRevoked'/)
    assert.equal((card.match(/canToggleActive\((?:token|tok)\)/g) || []).length, 2, 'eigene Liste und Admin-Liste')
    assert.equal((card.match(/dyndnsActionError\(e, t\)/g) || []).length, 2, 'Zeilen-Aktionen und Admin-Liste')
    assert.match(card, /dyndns\.secretRevokedHint/)
    assert.match(card, /dyndns\.secretRevokedRotate/)
    const modal = read('components/dyndns/DyndnsTokenModal.jsx')
    assert.match(modal, /dyndnsActionError\(e2, t\)/)
    assert.match(modal, /disabled=\{revoked && !form\.isActive\}/)
})
