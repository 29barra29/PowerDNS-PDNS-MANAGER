/**
 * Gemeinsame Validatoren, RECORD_TYPES und Vorlagen für die Zonendetail-Ansicht.
 */
import i18n from '../i18n'

const _t = (key, vars) => i18n.t(key, vars)

const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/
const IPV6_RE = /^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|::)$/
const FQDN_RE = /^(?=.{1,253}\.?$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$/i
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
    if (!IPV6_RE.test(s)) return { error: _t('zoneDetail.invalidIpv6') }
    return ''
}

function validateFqdn(v, { allowTrailingDot = true } = {}) {
    let s = (v || '').trim().replace(/\.$/, '')
    if (!s) return { error: _t('zoneDetail.enterHostname') }
    if (!FQDN_RE.test(s + (allowTrailingDot ? '.' : ''))) {
        return { error: _t('zoneDetail.invalidHostname') }
    }
    return ''
}

function validateInt(v, { min, max } = {}) {
    const s = String(v ?? '').trim()
    if (!s) return { error: _t('zoneDetail.enterNumber') }
    if (!/^\d+$/.test(s)) return { error: _t('zoneDetail.onlyDigits') }
    const n = parseInt(s, 10)
    if (typeof min === 'number' && n < min) return { error: _t('zoneDetail.minValue', { min, defaultValue: `Minimum: ${min}` }) }
    if (typeof max === 'number' && n > max) return { error: _t('zoneDetail.maxValue', { max, defaultValue: `Maximum: ${max}` }) }
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

function validateCaaTag(v) {
    const ok = ['issue', 'issuewild', 'iodef', 'contactemail', 'contactphone']
    if (!v) return { error: _t('zoneDetail.tagMissing') }
    if (!ok.includes(v)) return _t('zoneDetail.unusualCaaTag', { tag: v, common: ok.slice(0, 3).join(', ') })
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
        mailserver: (v) => validateFqdn(v),
    },
    SRV: {
        pri: (v) => validateInt(v, { min: 0, max: 65535 }),
        weight: (v) => validateInt(v, { min: 0, max: 65535 }),
        port: (v) => validateInt(v, { min: 1, max: 65535 }),
        target: (v) => validateFqdn(v),
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
    CNAME: () => _t('zoneDetail.apexCnameForbidden', { defaultValue: 'CNAME ist am Apex (Zonen-Wurzel) laut RFC nicht erlaubt. Nutze stattdessen ALIAS oder einen direkten A/AAAA-Record.' }),
    DS: () => _t('zoneDetail.apexDsForbidden', { defaultValue: 'DS ist am Apex einer eigenen Zone nicht erlaubt – DS-Records gehören in die ELTERN-Zone (also bei deinem Domain-Registrar). Hier kannst du DNSKEY-Einträge anlegen.' }),
    DNAME: () => _t('zoneDetail.apexDnameWarning', { defaultValue: 'DNAME am Apex ist meist falsch – nutze CNAME für Subdomains oder ALIAS am Apex.' }),
}

export function getApexWarning(name, type) {
    if (name !== '@') return null
    const msg = APEX_FORBIDDEN[type]
    if (msg) return { kind: 'error', text: msg() }
    if (type === 'PTR') return { kind: 'warn', text: _t('zoneDetail.apexPtrWarning', { defaultValue: 'PTR am Apex passt meist nur in Reverse-Zonen (in-addr.arpa).' }) }
    return null
}

export function buildQuickTemplates(zoneName) {
    return [
        {
            id: 'spf',
            label: _t('zoneDetail.quickSpfLabel', { defaultValue: 'SPF (Mail-Spoof-Schutz)' }),
            type: 'TXT',
            name: '@',
            ttl: '3600',
            fields: { text: 'v=spf1 mx -all' },
            note: _t('zoneDetail.quickSpfNote', { defaultValue: 'Trag bei mx oder include die zum Mailversand berechtigten Hosts ein.' }),
        },
        {
            id: 'dmarc',
            label: _t('zoneDetail.quickDmarcLabel', { defaultValue: 'DMARC (Reporting / Policy)' }),
            type: 'TXT',
            name: '_dmarc',
            ttl: '3600',
            fields: { text: `v=DMARC1; p=quarantine; rua=mailto:postmaster@${zoneName}` },
            note: _t('zoneDetail.quickDmarcNote', { defaultValue: 'Mit p=none startest du im Monitor-Modus.' }),
        },
        {
            id: 'dkim',
            label: _t('zoneDetail.quickDkimLabel', { defaultValue: 'DKIM (Selector default._domainkey)' }),
            type: 'TXT',
            name: 'default._domainkey',
            ttl: '3600',
            fields: { text: 'v=DKIM1; k=rsa; p=DEIN_BASE64_PUBLIC_KEY' },
            note: _t('zoneDetail.quickDkimNote', { defaultValue: 'Den öffentlichen Schlüssel stellt dein Mailserver bereit.' }),
        },
        {
            id: 'mta-sts',
            label: _t('zoneDetail.quickMtaStsLabel', { defaultValue: 'MTA-STS (Mail-Transport-Sicherheit)' }),
            type: 'TXT',
            name: '_mta-sts',
            ttl: '3600',
            fields: { text: 'v=STSv1; id=20240101000000Z' },
            note: _t('zoneDetail.quickMtaStsNote', { defaultValue: 'Erfordert zusätzlich /.well-known/mta-sts.txt unter mta-sts.<deine-domain>.' }),
        },
        {
            id: 'tls-rpt',
            label: _t('zoneDetail.quickTlsRptLabel', { defaultValue: 'TLS-RPT (TLS-Reports per Mail)' }),
            type: 'TXT',
            name: '_smtp._tls',
            ttl: '3600',
            fields: { text: `v=TLSRPTv1; rua=mailto:postmaster@${zoneName}` },
            note: '',
        },
        {
            id: 'caa',
            label: _t('zoneDetail.quickCaaLabel', { defaultValue: 'CAA (nur Let’s Encrypt darf Zertifikate ausstellen)' }),
            type: 'CAA',
            name: '@',
            ttl: '3600',
            fields: { flag: '0', tag: 'issue', val: 'letsencrypt.org' },
            note: '',
        },
        {
            id: 'tlsa-mail',
            label: _t('zoneDetail.quickTlsaMailLabel', { defaultValue: 'TLSA für SMTP (Port 25)' }),
            type: 'TLSA',
            name: '_25._tcp.mail',
            ttl: '3600',
            fields: { usage: '3', sel: '1', match: '1', hash: 'DEIN_SHA256_FINGERPRINT' },
            note: _t('zoneDetail.quickTlsaMailNote', { defaultValue: 'usage=3, sel=1, match=1 = DANE-EE / SPKI / SHA-256' }),
        },
    ]
}

function rdataRecord(typeKey, labelKey) {
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
            { id: 'flag', labelKey: 'zoneDetail.fieldFlag', label: 'Flag', placeholder: '0', type: 'number' },
            { id: 'tag', labelKey: 'zoneDetail.fieldTag', label: 'Tag', placeholder: 'issue', select: ['issue', 'issuewild', 'iodef'] },
            { id: 'val', labelKey: 'zoneDetail.fieldVal', label: 'Wert', placeholder: 'letsencrypt.org' },
        ], build: f => `${f.flag} ${f.tag} "${f.val}"`,
        parse: c => { const s = c.split(' '); return { flag: s[0], tag: s[1], val: s.slice(2).join(' ').replace(/"/g, '') } }
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

export const MULTI_VALUE_OK = new Set(['A', 'AAAA', 'NS', 'TXT', 'MX', 'CAA', 'SRV'])
