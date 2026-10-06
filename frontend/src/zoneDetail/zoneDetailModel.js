/**
 * Gemeinsame Validatoren, RECORD_TYPES und Vorlagen für die Zonendetail-Ansicht.
 *
 * Validatoren: FIELD_VALIDATORS[typ][feld](wert, set) -> '' | Warn-String | { error }. `set` ist das ganze Wert-Set
 * des Formulars (z. B. fuer Felder, deren Pruefung von einem anderen Feld abhaengt, F15 LUA).
 * Record-Definitionen: immer ueber getRecordDef(typ) nachschlagen – unbekannte Typen (z. B. CERT/URI aus einem
 * Import) bekommen einen RDATA-Texteditor (F8-F04). Felder koennen `default` (Startwert, defaultFieldSet) und
 * `select` (Auswahl) haben.
 * LUA (F15): Ziel-Typ (Auswahl, Default A) + Lua-Code; `lua: true` markiert die Definition. Vorlagen, Syntax-Hilfe,
 * Warnbox und Zeichenzaehler liefert die Formular-Erweiterung form-extensions/lua.ext.jsx; die reine Logik liegt in
 * lib/luaRecord.js.
 */
import i18n from '../i18n'
import { IPV4_RE, isValidIPv6 } from '../lib/ip.js'
import {
    CAA_COMMON_TAGS, CAA_TAGS, buildCaa, classifyHostname, defaultFieldSet, parseCaa,
} from '../lib/recordContent.js'
import {
    LUA_MAX_CONTENT_LENGTH, LUA_TARGET_TYPES, analyzeLuaCode, buildLuaContent, parseLuaContent,
} from '../lib/luaRecord.js'

export { defaultFieldSet }

const _t = (key, vars) => i18n.t(key, vars)

const HEX_RE = /^[0-9a-fA-F]+$/

function validateIPv4(v) {
    const s = (v || '').trim()
    if (!s) return { error: _t('zoneDetail.enterIpv4') }
    if (!IPV4_RE.test(s)) return { error: _t('zoneDetail.invalidIpv4') }
    return ''
}

function validateIPv6(v) {
    const s = (v || '').trim()
    if (!s) return { error: _t('zoneDetail.enterIpv6') }
    if (!isValidIPv6(s)) return { error: _t('zoneDetail.invalidIpv6') }
    return ''
}

// Hostname-Ziel (F8-F10): '.' ist nur mit allowRoot erlaubt (Null-MX/SRV, RFC 7505/2782) und nur ein Hinweis;
// einlabelige Namen sind eine Warnung, kein Fehler; Punycode-TLDs sind gueltig.
function validateFqdn(v, { allowRoot = false } = {}) {
    switch (classifyHostname(v, { allowRoot })) {
        case 'root': return _t('zoneDetail.nullTargetHint')
        case 'empty': return { error: _t('zoneDetail.enterHostname') }
        case 'valid': return ''
        case 'singleLabel': return _t('zoneDetail.singleLabelHostWarning')
        default: return { error: _t('zoneDetail.invalidHostname') }
    }
}

function validateInt(v, { min, max } = {}) {
    const s = String(v ?? '').trim()
    if (!s) return { error: _t('zoneDetail.enterNumber') }
    if (!/^\d+$/.test(s)) return { error: _t('zoneDetail.onlyDigits') }
    const n = parseInt(s, 10)
    if (typeof min === 'number' && n < min) return { error: _t('zoneDetail.minValue', { min }) }
    if (typeof max === 'number' && n > max) return { error: _t('zoneDetail.maxValue', { max }) }
    return ''
}

function validateHex(v) {
    const s = (v || '').replace(/\s+/g, '')
    if (!s) return { error: _t('zoneDetail.enterHex') }
    if (!HEX_RE.test(s)) return { error: _t('zoneDetail.onlyHex') }
    if (s.length % 2 !== 0) return _t('zoneDetail.hexLengthEven')
    return ''
}

function validateTxt(v) {
    const s = (v || '').trim()
    if (!s) return { error: _t('zoneDetail.enterText') }
    if (s.length > 255) return _t('zoneDetail.txtTooLong', { count: s.length })
    return ''
}

/** LUA-Code (F15 6.3): erster Fehler als { error }, sonst die erste Warnung als Text, sonst ''. */
export function luaCodeHint(rtype, code) {
    const { errors, warnings } = analyzeLuaCode(rtype, code)
    if (errors.length) return { error: _t(`lua.err.${errors[0]}`, { max: LUA_MAX_CONTENT_LENGTH }) }
    if (warnings.length) return _t(`lua.warn.${warnings[0]}`)
    return ''
}

function validateCaaTag(v) {
    if (!v) return { error: _t('zoneDetail.tagMissing') }
    if (!CAA_TAGS.includes(v)) return _t('zoneDetail.unusualCaaTag', { tag: v, common: CAA_COMMON_TAGS.join(', ') })
    return ''
}

export const FIELD_VALIDATORS = {
    A: { ipv4: validateIPv4 },
    AAAA: { ipv6: validateIPv6 },
    CNAME: { target: (v) => validateFqdn(v) },
    NS: { ns: (v) => validateFqdn(v) },
    PTR: { host: (v) => validateFqdn(v) },
    MX: {
        priority: (v) => validateInt(v, { min: 0, max: 65535 }),
        mailserver: (v) => validateFqdn(v, { allowRoot: true }),
    },
    SRV: {
        pri: (v) => validateInt(v, { min: 0, max: 65535 }),
        weight: (v) => validateInt(v, { min: 0, max: 65535 }),
        port: (v) => validateInt(v, { min: 1, max: 65535 }),
        target: (v) => validateFqdn(v, { allowRoot: true }),
    },
    TXT: { text: validateTxt },
    CAA: {
        flag: (v) => validateInt(v, { min: 0, max: 255 }),
        tag: validateCaaTag,
    },
    TLSA: {
        usage: (v) => validateInt(v, { min: 0, max: 3 }),
        sel: (v) => validateInt(v, { min: 0, max: 1 }),
        match: (v) => validateInt(v, { min: 0, max: 2 }),
        hash: validateHex,
    },
    LUA: {
        // Ziel-Typ-Fehler meldet das Code-Feld mit (analyzeLuaCode prueft beides)
        code: (v, set) => luaCodeHint(set?.rtype || 'A', v),
    },
    SSHFP: {
        algo: (v) => validateInt(v, { min: 1, max: 6 }),
        fptype: (v) => validateInt(v, { min: 1, max: 2 }),
        fp: validateHex,
    },
    SOA: {
        mname: (v) => validateFqdn(v),
        rname: (v) => validateFqdn(v),
        serial: (v) => validateInt(v, { min: 0 }),
        refresh: (v) => validateInt(v, { min: 0 }),
        retry: (v) => validateInt(v, { min: 0 }),
        expire: (v) => validateInt(v, { min: 0 }),
        minimum: (v) => validateInt(v, { min: 0 }),
    },
}

const APEX_FORBIDDEN = {
    CNAME: () => _t('zoneDetail.apexCnameForbidden'),
    DS: () => _t('zoneDetail.apexDsForbidden'),
    DNAME: () => _t('zoneDetail.apexDnameWarning'),
}

export function getApexWarning(name, type) {
    if (name !== '@') return null
    const msg = APEX_FORBIDDEN[type]
    if (msg) return { kind: 'error', text: msg() }
    if (type === 'PTR') return { kind: 'warn', text: _t('zoneDetail.apexPtrWarning') }
    return null
}

export function buildQuickTemplates(zoneName) {
    return [
        {
            id: 'spf',
            label: _t('zoneDetail.quickSpfLabel'),
            type: 'TXT',
            name: '@',
            ttl: '3600',
            fields: { text: 'v=spf1 mx -all' },
            note: _t('zoneDetail.quickSpfNote'),
        },
        {
            id: 'dmarc',
            label: _t('zoneDetail.quickDmarcLabel'),
            type: 'TXT',
            name: '_dmarc',
            ttl: '3600',
            fields: { text: `v=DMARC1; p=quarantine; rua=mailto:postmaster@${zoneName}` },
            note: _t('zoneDetail.quickDmarcNote'),
        },
        {
            id: 'dkim',
            label: _t('zoneDetail.quickDkimLabel'),
            type: 'TXT',
            name: 'default._domainkey',
            ttl: '3600',
            fields: { text: 'v=DKIM1; k=rsa; p=DEIN_BASE64_PUBLIC_KEY' },
            note: _t('zoneDetail.quickDkimNote'),
        },
        {
            id: 'mta-sts',
            label: _t('zoneDetail.quickMtaStsLabel'),
            type: 'TXT',
            name: '_mta-sts',
            ttl: '3600',
            fields: { text: 'v=STSv1; id=20240101000000Z' },
            note: _t('zoneDetail.quickMtaStsNote'),
        },
        {
            id: 'tls-rpt',
            label: _t('zoneDetail.quickTlsRptLabel'),
            type: 'TXT',
            name: '_smtp._tls',
            ttl: '3600',
            fields: { text: `v=TLSRPTv1; rua=mailto:postmaster@${zoneName}` },
            note: '',
        },
        {
            id: 'caa',
            label: _t('zoneDetail.quickCaaLabel'),
            type: 'CAA',
            name: '@',
            ttl: '3600',
            fields: { flag: '0', tag: 'issue', val: 'letsencrypt.org' },
            note: '',
        },
        {
            id: 'tlsa-mail',
            label: _t('zoneDetail.quickTlsaMailLabel'),
            type: 'TLSA',
            name: '_25._tcp.mail',
            ttl: '3600',
            fields: { usage: '3', sel: '1', match: '1', hash: 'DEIN_SHA256_FINGERPRINT' },
            note: _t('zoneDetail.quickTlsaMailNote'),
        },
    ]
}

function rdataRecord(typeKey, labelKey) {
    // placeholderKey ist dynamisch (zoneDetail.rdataPh<TYP>); gerendert mit defaultValue '' (F8-F04)
    return {
        labelKey,
        rdataTypeKey: typeKey,
        fields: [
            {
                id: 'raw',
                labelKey: 'zoneDetail.fieldRdata',
                textarea: true,
                placeholderKey: `zoneDetail.rdataPh${typeKey}`,
            },
        ],
        build: (f) => f.raw.trim(),
        parse: (c) => ({ raw: c }),
    }
}

export const RECORD_TYPES = {
    A: { labelKey: 'zoneDetail.recordA', label: 'A – IPv4', fields: [{ id: 'ipv4', labelKey: 'zoneDetail.fieldIpv4', label: 'IPv4-Adresse', placeholder: '93.184.216.34' }], build: f => f.ipv4, parse: c => ({ ipv4: c }) },
    AAAA: { labelKey: 'zoneDetail.recordAAAA', label: 'AAAA – IPv6', fields: [{ id: 'ipv6', labelKey: 'zoneDetail.fieldIpv6', label: 'IPv6-Adresse', placeholder: '2001:db8::1' }], build: f => f.ipv6, parse: c => ({ ipv6: c }) },
    CNAME: { labelKey: 'zoneDetail.recordCNAME', label: 'CNAME – Weiterleitung', fields: [{ id: 'target', labelKey: 'zoneDetail.fieldTarget', label: 'Ziel-Domain', placeholder: 'example.com.' }], build: f => f.target.endsWith('.') ? f.target : f.target + '.', parse: c => ({ target: c }) },
    MX: {
        labelKey: 'zoneDetail.recordMX', label: 'MX – Mailserver', fields: [
            { id: 'priority', labelKey: 'zoneDetail.fieldPriority', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'mailserver', labelKey: 'zoneDetail.fieldMailserver', label: 'Mail-Server', placeholder: 'mail.example.com.' },
        ], build: f => `${f.priority} ${f.mailserver.endsWith('.') ? f.mailserver : f.mailserver + '.'}`,
        parse: c => { const s = c.split(' '); return { priority: s[0], mailserver: s[1] } }
    },
    TXT: { labelKey: 'zoneDetail.recordTXT', label: 'TXT – Text', fields: [{ id: 'text', labelKey: 'zoneDetail.fieldText', label: 'Text', placeholder: 'v=spf1 ...', textarea: true }], build: f => f.text.startsWith('"') ? f.text : `"${f.text}"`, parse: c => { let t = c; if (t.startsWith('"') && t.endsWith('"')) t = t.substring(1, t.length - 1); return { text: t } } },
    NS: { labelKey: 'zoneDetail.recordNS', label: 'NS – Nameserver', fields: [{ id: 'ns', labelKey: 'zoneDetail.fieldNs', label: 'Nameserver', placeholder: 'ns1.example.com.' }], build: f => f.ns.endsWith('.') ? f.ns : f.ns + '.', parse: c => ({ ns: c }) },
    SOA: {
        labelKey: 'zoneDetail.recordSOA', label: 'SOA – Start of Authority', fields: [
            { id: 'mname', labelKey: 'zoneDetail.fieldMname', label: 'Primary NS', placeholder: 'ns1.example.com.' },
            { id: 'rname', labelKey: 'zoneDetail.fieldRname', label: 'Hostmaster Email', placeholder: 'hostmaster.example.com.' },
            { id: 'serial', labelKey: 'zoneDetail.fieldSerial', label: 'Serial', type: 'number' },
            { id: 'refresh', labelKey: 'zoneDetail.fieldRefresh', label: 'Refresh', type: 'number' },
            { id: 'retry', labelKey: 'zoneDetail.fieldRetry', label: 'Retry', type: 'number' },
            { id: 'expire', labelKey: 'zoneDetail.fieldExpire', label: 'Expire', type: 'number' },
            { id: 'minimum', labelKey: 'zoneDetail.fieldMinimum', label: 'Minimum TTL', type: 'number' },
        ], build: f => `${f.mname.endsWith('.') ? f.mname : f.mname + '.'} ${f.rname.endsWith('.') ? f.rname : f.rname + '.'} ${f.serial} ${f.refresh} ${f.retry} ${f.expire} ${f.minimum}`,
        parse: c => { const s = c.split(' '); return { mname: s[0], rname: s[1], serial: s[2], refresh: s[3], retry: s[4], expire: s[5], minimum: s[6] } }
    },
    SRV: {
        labelKey: 'zoneDetail.recordSRV', label: 'SRV – Dienst', fields: [
            { id: 'pri', labelKey: 'zoneDetail.fieldPri', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'weight', labelKey: 'zoneDetail.fieldWeight', label: 'Gewicht', placeholder: '5', type: 'number' },
            { id: 'port', labelKey: 'zoneDetail.fieldPort', label: 'Port', placeholder: '443', type: 'number' },
            { id: 'target', labelKey: 'zoneDetail.fieldTarget', label: 'Ziel', placeholder: 'server.example.com.' },
        ], build: f => `${f.pri} ${f.weight} ${f.port} ${f.target.endsWith('.') ? f.target : f.target + '.'}`,
        parse: c => { const s = c.split(' '); return { pri: s[0], weight: s[1], port: s[2], target: s[3] } }
    },
    CAA: {
        labelKey: 'zoneDetail.recordCAA', label: 'CAA – Zertifikat', fields: [
            { id: 'flag', labelKey: 'zoneDetail.fieldFlag', label: 'Flag', placeholder: '0', type: 'number', default: '0' },
            { id: 'tag', labelKey: 'zoneDetail.fieldTag', label: 'Tag', placeholder: 'issue', select: [...CAA_TAGS], default: 'issue' },
            { id: 'val', labelKey: 'zoneDetail.fieldVal', label: 'Wert', placeholder: 'letsencrypt.org' },
        ],
        // Wert in Anfuehrungszeichen, " und \ escaped; parse ist das Gegenstueck (F8-F03, N19)
        build: f => buildCaa(f),
        parse: c => parseCaa(c),
    },
    PTR: { labelKey: 'zoneDetail.recordPTR', label: 'PTR – Reverse', fields: [{ id: 'host', labelKey: 'zoneDetail.fieldHost', label: 'Hostname', placeholder: 'host.example.com.' }], build: f => f.host.endsWith('.') ? f.host : f.host + '.', parse: c => ({ host: c }) },
    TLSA: {
        labelKey: 'zoneDetail.recordTLSA', label: 'TLSA – DANE', fields: [
            { id: 'usage', labelKey: 'zoneDetail.fieldUsage', label: 'Usage', placeholder: '3', type: 'number' },
            { id: 'sel', labelKey: 'zoneDetail.fieldSel', label: 'Selector', placeholder: '1', type: 'number' },
            { id: 'match', labelKey: 'zoneDetail.fieldMatch', label: 'Matching', placeholder: '1', type: 'number' },
            { id: 'hash', labelKey: 'zoneDetail.fieldHash', label: 'Hash', placeholder: 'abc123...' },
        ], build: f => `${f.usage} ${f.sel} ${f.match} ${f.hash}`,
        parse: c => { const s = c.split(' '); return { usage: s[0], sel: s[1], match: s[2], hash: s[3] } }
    },
    SSHFP: {
        labelKey: 'zoneDetail.recordSSHFP', label: 'SSHFP – SSH', fields: [
            { id: 'algo', labelKey: 'zoneDetail.fieldAlgo', label: 'Algo', placeholder: '4', type: 'number' },
            { id: 'fptype', labelKey: 'zoneDetail.fieldFptype', label: 'Hash-Typ', placeholder: '2', type: 'number' },
            { id: 'fp', labelKey: 'zoneDetail.fieldFp', label: 'Fingerprint', placeholder: 'abc...' },
        ], build: f => `${f.algo} ${f.fptype} ${f.fp}`,
        parse: c => { const s = c.split(' '); return { algo: s[0], fptype: s[1], fp: s[2] } }
    },
    ALIAS: rdataRecord('ALIAS', 'zoneDetail.recordALIAS'),
    LUA: {
        labelKey: 'zoneDetail.recordLUA', label: 'LUA', lua: true,
        fields: [
            { id: 'rtype', labelKey: 'lua.fieldTargetType', label: 'Ziel-Typ', select: [...LUA_TARGET_TYPES], default: 'A' },
            { id: 'code', labelKey: 'lua.fieldCode', label: 'Lua-Code', textarea: true, placeholderKey: 'lua.codePlaceholder' },
        ],
        build: (f) => buildLuaContent(f.rtype || 'A', f.code || ''),
        parse: (c) => { const p = parseLuaContent(c); return { rtype: p.rtype, code: p.code } },
    },
    DNAME: rdataRecord('DNAME', 'zoneDetail.recordDNAME'),
    LOC: rdataRecord('LOC', 'zoneDetail.recordLOC'),
    NAPTR: rdataRecord('NAPTR', 'zoneDetail.recordNAPTR'),
    DS: rdataRecord('DS', 'zoneDetail.recordDS'),
    DNSKEY: rdataRecord('DNSKEY', 'zoneDetail.recordDNSKEY'),
    NSEC: rdataRecord('NSEC', 'zoneDetail.recordNSEC'),
    NSEC3: rdataRecord('NSEC3', 'zoneDetail.recordNSEC3'),
    NSEC3PARAM: rdataRecord('NSEC3PARAM', 'zoneDetail.recordNSEC3PARAM'),
    RRSIG: rdataRecord('RRSIG', 'zoneDetail.recordRRSIG'),
    SPF: rdataRecord('SPF', 'zoneDetail.recordSPF'),
    HTTPS: rdataRecord('HTTPS', 'zoneDetail.recordHTTPS'),
    SVCB: rdataRecord('SVCB', 'zoneDetail.recordSVCB'),
    OPENPGPKEY: rdataRecord('OPENPGPKEY', 'zoneDetail.recordOPENPGPKEY'),
}

// LUA (F15 12.1-12): ein LUA-RRset darf mehrere Werte mit unterschiedlichen Ziel-Typen haben (z. B. A + AAAA)
export const MULTI_VALUE_OK = new Set(['A', 'AAAA', 'NS', 'TXT', 'MX', 'CAA', 'SRV', 'LUA'])

/**
 * Editor fuer Typen ohne eigenen Eintrag in RECORD_TYPES (z. B. CERT, URI, SMIMEA aus einem Import): RDATA als Text.
 * `unknown: true` -> Hinweis im Dialog, kein Klonen (das Backend nimmt beim Anlegen nur die Allowlist an).
 */
export function genericRecordDef(type) {
    const base = rdataRecord(type, null)
    return {
        ...base,
        label: type,
        unknown: true,
        fields: [{ id: 'raw', labelKey: 'zoneDetail.fieldRdata', textarea: true }],
    }
}

/** Record-Definition fuer einen Typ; nie undefined (unbekannte Typen -> genericRecordDef). */
export function getRecordDef(type) {
    return RECORD_TYPES[type] || genericRecordDef(String(type || ''))
}
