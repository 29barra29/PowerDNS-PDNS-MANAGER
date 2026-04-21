import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import {
    ArrowLeft, Plus, Trash2, Pencil, Loader2, AlertCircle, CheckCircle,
    Copy, X, Sparkles
} from 'lucide-react'
import api from '../api'
import i18n from '../i18n'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import DnsRecordTypeHint from '../components/DnsRecordTypeHint'

// Kuerze-Helper, damit die Validator-Funktionen unten lesbarer sind.
// Nutzt das globale i18n-Objekt – funktioniert auch ausserhalb der Component.
const _t = (key, vars) => i18n.t(key, vars)

/* ============================================================================
 *  Validierung pro Eingabefeld – wird LIVE im Modal angezeigt.
 *  Rückgabe: ''  → ok
 *            'msg' → Hinweis (kein Blocker)  – wird gelb angezeigt
 *            { error: 'msg' } → harter Fehler – wird rot angezeigt + blockt Save
 * ========================================================================== */

const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/
// Pragmatisches IPv6 – akzeptiert vollständige + komprimierte Schreibweise.
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
    if (typeof min === 'number' && n < min) return { error: `Minimum: ${min}` }
    if (typeof max === 'number' && n > max) return { error: `Maximum: ${max}` }
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

const FIELD_VALIDATORS = {
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

/* ============================================================================
 *  Apex-Schutz: Welche Typen sind direkt am Zone-Apex (@) verboten / unüblich?
 * ========================================================================== */
const APEX_FORBIDDEN = {
    CNAME: 'CNAME ist am Apex (Zonen-Wurzel) laut RFC nicht erlaubt. Nutze stattdessen ALIAS oder einen direkten A/AAAA-Record.',
    DS: 'DS ist am Apex einer eigenen Zone nicht erlaubt – DS-Records gehören in die ELTERN-Zone (also bei deinem Domain-Registrar). Hier kannst du DNSKEY-Einträge anlegen.',
    DNAME: 'DNAME am Apex ist meist falsch – nutze CNAME für Subdomains oder ALIAS am Apex.',
}

function getApexWarning(name, type) {
    if (name !== '@') return null
    const msg = APEX_FORBIDDEN[type]
    if (msg) return { kind: 'error', text: msg }
    if (type === 'PTR') return { kind: 'warn', text: 'PTR am Apex passt meist nur in Reverse-Zonen (in-addr.arpa).' }
    return null
}

/* ============================================================================
 *  Schnellvorlagen für oft genutzte Records
 * ========================================================================== */
function buildQuickTemplates(zoneName) {
    return [
        {
            id: 'spf',
            label: 'SPF (Mail-Spoof-Schutz)',
            type: 'TXT',
            name: '@',
            ttl: '3600',
            // Inhalt geht in den Text-Field; wird beim Save in "..." gewrappt
            fields: { text: 'v=spf1 mx -all' },
            note: 'Trag bei mx oder include die zum Mailversand berechtigten Hosts ein.',
        },
        {
            id: 'dmarc',
            label: 'DMARC (Reporting / Policy)',
            type: 'TXT',
            name: '_dmarc',
            ttl: '3600',
            fields: { text: `v=DMARC1; p=quarantine; rua=mailto:postmaster@${zoneName}` },
            note: 'Mit p=none startest du im Monitor-Modus.',
        },
        {
            id: 'dkim',
            label: 'DKIM (Selector default._domainkey)',
            type: 'TXT',
            name: 'default._domainkey',
            ttl: '3600',
            fields: { text: 'v=DKIM1; k=rsa; p=DEIN_BASE64_PUBLIC_KEY' },
            note: 'Den öffentlichen Schlüssel stellt dein Mailserver bereit.',
        },
        {
            id: 'mta-sts',
            label: 'MTA-STS (Mail-Transport-Sicherheit)',
            type: 'TXT',
            name: '_mta-sts',
            ttl: '3600',
            fields: { text: 'v=STSv1; id=20240101000000Z' },
            note: 'Erfordert zusätzlich /.well-known/mta-sts.txt unter mta-sts.<deine-domain>.',
        },
        {
            id: 'tls-rpt',
            label: 'TLS-RPT (TLS-Reports per Mail)',
            type: 'TXT',
            name: '_smtp._tls',
            ttl: '3600',
            fields: { text: `v=TLSRPTv1; rua=mailto:postmaster@${zoneName}` },
            note: '',
        },
        {
            id: 'caa',
            label: 'CAA (nur Let’s Encrypt darf Zertifikate ausstellen)',
            type: 'CAA',
            name: '@',
            ttl: '3600',
            fields: { flag: '0', tag: 'issue', val: 'letsencrypt.org' },
            note: '',
        },
        {
            id: 'tlsa-mail',
            label: 'TLSA für SMTP (Port 25)',
            type: 'TLSA',
            name: '_25._tcp.mail',
            ttl: '3600',
            fields: { usage: '3', sel: '1', match: '1', hash: 'DEIN_SHA256_FINGERPRINT' },
            note: 'usage=3, sel=1, match=1 = DANE-EE / SPKI / SHA-256',
        },
    ]
}

/** Ein Feld – freier RDATA-Text (PowerDNS-Format); placeholderKey = Kurzbeispiel */
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

const RECORD_TYPES = {
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

/** Welche Typen erlauben mehrere Werte in EINEM Modal (Round-Robin / mehrere RRset-Einträge)? */
const MULTI_VALUE_OK = new Set(['A', 'AAAA', 'NS', 'TXT', 'MX', 'CAA', 'SRV'])

export default function ZoneDetailPage() {
    const { t } = useTranslation()
    const { server, zoneId } = useParams()
    const navigate = useNavigate()
    const [_zone, setZone] = useState(null)
    const [records, setRecords] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [modalError, setModalError] = useState('')
    const [success, setSuccess] = useState('')
    const [showAdd, setShowAdd] = useState(false)
    const [isEdit, setIsEdit] = useState(false)
    const [oldContent, setOldContent] = useState('')
    const [addType, setAddType] = useState('A')
    const [addName, setAddName] = useState('@')
    const [addTTL, setAddTTL] = useState('3600')
    /** Liste von Wert-Sets (für Round-Robin). Beim Edit immer genau ein Set. */
    const [dynFieldsList, setDynFieldsList] = useState([{}])
    const [saving, setSaving] = useState(false)
    /** Ist beim Submit aktiv – verhindert nochmaliges Senden bei Strg+Enter */
    const formRef = useRef(null)

    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    function closeModal() {
        setShowAdd(false)
        setModalError('')
        setIsEdit(false)
        setOldContent('')
        setDynFieldsList([{}])
    }

    function openAddModal() {
        setIsEdit(false)
        setAddType('A')
        setAddName('@')
        setAddTTL('3600')
        setDynFieldsList([{}])
        setModalError('')
        setShowAdd(true)
    }

    /** Bestehenden Record klonen → öffnet Add-Modal mit denselben Werten, aber ohne oldContent. */
    function openClone(record) {
        setIsEdit(false)
        setOldContent('')
        setAddType(record.type)
        setAddTTL(record.ttl.toString())
        setModalError('')

        let name = record.name.replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        setAddName(name)

        const def = RECORD_TYPES[record.type] || { parse: () => ({}) }
        try { setDynFieldsList([def.parse ? def.parse(record.content) : {}]) } catch { setDynFieldsList([{}]) }
        setShowAdd(true)
    }

    /** Schnellvorlage einfügen */
    function applyQuickTemplate(tpl) {
        setIsEdit(false)
        setOldContent('')
        setAddType(tpl.type)
        setAddName(tpl.name)
        setAddTTL(tpl.ttl)
        setDynFieldsList([{ ...tpl.fields }])
        setModalError('')
        setShowAdd(true)
    }

    const zoneName = zoneId.replace(/\.$/, '')
    const quickTemplates = useMemo(() => buildQuickTemplates(zoneName), [zoneName])

    useEffect(() => { loadZone() }, [server, zoneId]) // eslint-disable-line react-hooks/exhaustive-deps -- loadZone stable

    /** ESC schließt Modal, Strg/Cmd+Enter speichert */
    useEffect(() => {
        if (!showAdd) return
        function onKey(e) {
            if (e.key === 'Escape') {
                if (!saving) closeModal()
            } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                if (formRef.current && !saving) formRef.current.requestSubmit()
            }
        }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [showAdd, saving])  

    async function loadZone() {
        setLoading(true)
        try {
            const data = await api.listRecords(server, zoneId)
            setRecords(data.records || [])
            setZone(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    function resolveName(name) {
        let fqdn
        if (name === '@') fqdn = zoneName
        else if (!name.includes(zoneName)) fqdn = `${name}.${zoneName}`
        else fqdn = name
        if (!fqdn.endsWith('.')) fqdn = fqdn + '.'
        return fqdn
    }

    function previewFqdn(name) {
        return resolveName((name || '').trim() || '@')
    }

    function openEdit(record) {
        setIsEdit(true)
        setAddType(record.type)
        setOldContent(record.content)
        setAddTTL(record.ttl.toString())
        setModalError('')

        let name = record.name.replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        setAddName(name)

        const def = RECORD_TYPES[record.type] || { parse: () => ({}) }
        try {
            setDynFieldsList([def.parse ? def.parse(record.content) : {}])
        } catch (e) {
            console.warn('Could not parse record content:', e)
            setDynFieldsList([{}])
        }
        setShowAdd(true)
    }

    /* ----- Live-Validierung -------------------------------------------------*/
    const apexWarning = getApexWarning(addName.trim() || '@', addType)

    /** Validatoren für das aktuell ausgewählte Type – pro Field. */
    const validators = FIELD_VALIDATORS[addType] || {}

    function fieldHint(fieldId, value) {
        const v = validators[fieldId]
        if (!v) return ''
        return v(value)
    }

    /** Existiert ein Record mit (name,type,content) bereits? */
    function findDuplicate(name, type, content) {
        const fqdn = resolveName(name)
        return records.find(r => r.name === fqdn && r.type === type && r.content === content)
    }

    /* ----- Submit -----------------------------------------------------------*/
    async function handleAddRecord(e) {
        e.preventDefault()
        if (saving) return
        setSaving(true)
        setModalError('')

        const def = RECORD_TYPES[addType]
        if (!def) { setSaving(false); return }

        if (apexWarning?.kind === 'error') {
            setModalError(apexWarning.text)
            setSaving(false)
            return
        }

        // Hartfehler in Feldern blocken den Submit
        for (let i = 0; i < dynFieldsList.length; i++) {
            const set = dynFieldsList[i] || {}
            for (const f of def.fields) {
                const val = set[f.id]
                if (val === undefined || String(val).trim() === '') {
                    setModalError(t('zoneDetail.fillField', { label: f.labelKey ? t(f.labelKey) : f.label }))
                    setSaving(false)
                    return
                }
                const hint = fieldHint(f.id, val)
                if (hint && typeof hint === 'object' && hint.error) {
                    const lbl = f.labelKey ? t(f.labelKey) : f.label
                    setModalError(`${lbl}${dynFieldsList.length > 1 ? ` (Wert ${i + 1})` : ''}: ${hint.error}`)
                    setSaving(false)
                    return
                }
            }
        }

        // Werte bauen
        const contents = []
        for (const set of dynFieldsList) {
            try { contents.push(def.build(set)) } catch (err) {
                setModalError(`Wert konnte nicht gebaut werden: ${err.message || err}`)
                setSaving(false)
                return
            }
        }

        const fqdn = resolveName(addName)

        // Duplikat-Check (nur Add, nicht beim Edit eines bestehenden Eintrags)
        if (!isEdit) {
            for (const c of contents) {
                const dup = findDuplicate(addName, addType, c)
                if (dup) {
                    setModalError(`Es existiert bereits ein ${addType}-Eintrag „${dup.name.replace(/\.$/, '')}“ mit dem Wert „${c}“.`)
                    setSaving(false)
                    return
                }
            }
        }

        try {
            if (isEdit) {
                await api.updateRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    old_content: oldContent,
                    new_content: contents[0],
                    disabled: false,
                })
            } else {
                await api.createRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    records: contents.map(c => ({ content: c, disabled: false })),
                })
            }
            closeModal()
            const displayName = fqdn.replace(/\.$/, '')
            setSuccess(isEdit
                ? t('zoneDetail.recordUpdated', { type: addType, name: displayName })
                : t('zoneDetail.recordCreated', { type: addType, name: displayName }))
            loadZone()
        } catch (err) {
            setModalError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(name, type) {
        if (!confirm(t('zoneDetail.deleteRecordConfirm', { name, type }))) return
        try {
            await api.deleteRecord(server, zoneId, { name, type })
            setSuccess(t('zoneDetail.recordDeleted', { type, name: name.replace(/\.$/, '') }))
            loadZone()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    const grouped = {}
    records.forEach(r => {
        if (!grouped[r.type]) grouped[r.type] = []
        grouped[r.type].push(r)
    })
    const typeOrder = ALL_RECORD_TYPE_KEYS
    const sortedTypes = Object.keys(grouped).sort((a, b) => {
        const ai = typeOrder.indexOf(a), bi = typeOrder.indexOf(b)
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
    })

    const canMulti = !isEdit && MULTI_VALUE_OK.has(addType)
    const def = RECORD_TYPES[addType]

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button onClick={() => navigate('/zones')} className="p-2 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors">
                        <ArrowLeft className="w-5 h-5" />
                    </button>
                    <div>
                        <h1 className="text-2xl font-bold text-text-primary">{zoneName}</h1>
                        <p className="text-text-muted text-sm">{t('zoneDetail.recordsCount', { server, count: records.length })}</p>
                    </div>
                </div>
                <button
                    onClick={openAddModal}
                    className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                >
                    <Plus className="w-4 h-4" /> {t('zoneDetail.addRecord')}
                </button>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                    <CheckCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm flex-1">{success}</p>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {/* Records grouped by type */}
            {sortedTypes.map(type => (
                <div key={type} className="glass-card overflow-hidden">
                    <div className="px-4 py-3 bg-bg-hover/30 border-b border-border flex items-center gap-2">
                        <span className="text-xs font-bold px-2 py-0.5 bg-accent/20 text-accent-light rounded">{type}</span>
                        <span className="text-xs text-text-muted">{t('zoneDetail.recordCount', { count: grouped[type].length })}</span>
                    </div>
                    <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[640px]">
                        <thead>
                            <tr className="border-b border-border/50">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">{t('zoneDetail.ttl')}</th>
                                <th className="text-right p-3 text-text-muted font-medium text-xs w-28">{t('zones.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r, i) => (
                                <tr key={i} className="border-b border-border/30 hover:bg-bg-hover/30 transition-colors">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name.replace(/\.$/, '')}</td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 text-text-muted text-xs">{r.ttl}</td>
                                    <td className="p-3 text-right whitespace-nowrap">
                                        <button
                                            onClick={() => openEdit(r)}
                                            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1"
                                            title={t('zoneDetail.edit')}
                                        >
                                            <Pencil className="w-3.5 h-3.5" />
                                        </button>
                                        {type !== 'SOA' && (
                                            <button
                                                onClick={() => openClone(r)}
                                                className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1"
                                                title={t('zoneDetail.clone')}
                                            >
                                                <Copy className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                        {type !== 'SOA' && type !== 'NS' && (
                                            <button
                                                onClick={() => handleDelete(r.name, r.type)}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                title={t('zoneDetail.deleteRecord')}
                                            >
                                                <Trash2 className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    </div>
                </div>
            ))}

            {/* Add / Edit Record Modal */}
            {showAdd && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                    onClick={() => { if (!saving) closeModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-3xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-start justify-between mb-4">
                            <h2 className="text-lg font-bold text-text-primary">
                                {isEdit ? t('zoneDetail.editRecord') : t('zoneDetail.addRecord')}
                            </h2>
                            <button
                                type="button"
                                onClick={() => { if (!saving) closeModal() }}
                                className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                                title={t('common.close')}
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {/* Schnellvorlagen-Leiste (nur beim Anlegen) */}
                        {!isEdit && (
                            <div className="mb-4 rounded-xl border border-accent/20 bg-accent/5 p-3">
                                <div className="flex items-center gap-2 mb-2">
                                    <Sparkles className="w-4 h-4 text-accent" />
                                    <span className="text-xs font-medium text-text-secondary">{t('zoneDetail.quickTemplates')}</span>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {quickTemplates.map(tpl => (
                                        <button
                                            key={tpl.id}
                                            type="button"
                                            onClick={() => applyQuickTemplate(tpl)}
                                            className="text-xs px-2.5 py-1 rounded-md border border-border bg-bg-primary hover:bg-bg-hover transition-colors"
                                            title={tpl.note || tpl.label}
                                        >
                                            {tpl.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Modal-weiter Fehler (Submit / Backend) */}
                        {modalError && (
                            <div className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">
                                        {isEdit ? t('zoneDetail.updateErrorTitle') : t('zoneDetail.createErrorTitle')}
                                    </p>
                                    <p className="text-sm mt-1 break-words">{modalError}</p>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => setModalError('')}
                                    className="text-xs hover:underline shrink-0"
                                    aria-label={t('common.close')}
                                >
                                    ×
                                </button>
                            </div>
                        )}

                        {/* Apex-Warnung (live, nicht erst beim Submit) */}
                        {apexWarning && (
                            <div className={`mb-4 p-3 rounded-xl border flex items-start gap-2 ${apexWarning.kind === 'error' ? 'bg-danger/10 border-danger/30 text-danger' : 'bg-amber-500/10 border-amber-500/30 text-amber-200'}`}>
                                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                                <div className="text-xs">
                                    <p className="font-medium mb-0.5">{t('zoneDetail.apexWarningTitle')}</p>
                                    <p>{apexWarning.text}</p>
                                </div>
                            </div>
                        )}

                        <form ref={formRef} onSubmit={handleAddRecord} className="space-y-4">
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 items-start">
                                <div className="min-w-0 flex flex-col gap-1">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.recordType')}</label>
                                    <select value={addType} disabled={isEdit} onChange={e => { setAddType(e.target.value); setDynFieldsList([{}]) }} className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50">
                                        {ALL_RECORD_TYPE_KEYS.filter((k) => RECORD_TYPES[k]).map((k) => {
                                            const v = RECORD_TYPES[k]
                                            return <option key={k} value={k}>{v.labelKey ? t(v.labelKey) : v.label}</option>
                                        })}
                                    </select>
                                </div>
                                <div className="min-w-0 flex flex-col gap-1.5">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.nameRelative')}</label>
                                    <input
                                        value={addName}
                                        disabled={isEdit}
                                        onChange={(e) => setAddName(e.target.value)}
                                        className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
                                        placeholder="@"
                                        autoComplete="off"
                                        spellCheck={false}
                                    />
                                    <div className="rounded-lg border border-accent/25 bg-accent/5 px-2.5 py-2">
                                        <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted mb-0.5">{t('zoneDetail.namePreviewLabel')}</p>
                                        <p className="font-mono text-sm text-accent-light break-all" title={previewFqdn(addName)}>{previewFqdn(addName)}</p>
                                    </div>
                                    <p className="text-xs text-text-muted leading-snug">{t('zoneDetail.mainDomainHint')}</p>
                                </div>
                                <div className="min-w-0 flex flex-col gap-1">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.ttl')}</label>
                                    <select value={addTTL} onChange={e => setAddTTL(e.target.value)} className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary">
                                        <option value="60">1 Min</option>
                                        <option value="300">5 Min</option>
                                        <option value="3600">1 Std</option>
                                        <option value="14400">4 Std</option>
                                        <option value="86400">1 Tag</option>
                                    </select>
                                </div>
                            </div>

                            {/* Dynamische Werte – ggf. mehrere für Round-Robin */}
                            <div className="border-t border-border pt-4 space-y-4">
                                {dynFieldsList.map((set, idx) => {
                                    const fldList = def?.fields || []
                                    return (
                                        <div key={idx} className={dynFieldsList.length > 1 ? 'rounded-lg border border-border/60 p-3 bg-bg-primary/50' : ''}>
                                            {dynFieldsList.length > 1 && (
                                                <div className="flex items-center justify-between mb-2">
                                                    <span className="text-xs font-medium text-text-muted">{t('zoneDetail.valueIndex', { n: idx + 1 })}</span>
                                                    <button
                                                        type="button"
                                                        onClick={() => setDynFieldsList(list => list.filter((_, i) => i !== idx))}
                                                        className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                        title={t('zoneDetail.removeValue')}
                                                    >
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </button>
                                                </div>
                                            )}
                                            <div className={`grid gap-4 ${
                                                addType === 'SRV'
                                                    ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
                                                    : fldList.length === 1 && fldList[0].textarea
                                                        ? 'grid-cols-1'
                                                        : 'grid-cols-1 sm:grid-cols-2'
                                            }`}>
                                                {fldList.map((f) => {
                                                    const oneTextareaOnly = fldList.length === 1 && fldList[0].textarea
                                                    const textareaSpan = f.textarea
                                                        ? (oneTextareaOnly && addType === 'TXT' ? 'sm:col-span-2' : oneTextareaOnly ? '' : 'sm:col-span-2 lg:col-span-4')
                                                        : ''
                                                    const value = set[f.id] || ''
                                                    const hint = fieldHint(f.id, value)
                                                    const hintText = typeof hint === 'object' && hint?.error ? hint.error : (typeof hint === 'string' ? hint : '')
                                                    const isError = typeof hint === 'object' && !!hint?.error
                                                    return (
                                                        <div key={f.id} className={`min-w-0 ${textareaSpan}`}>
                                                            <label className="block text-xs font-medium text-text-secondary mb-1">{f.labelKey ? t(f.labelKey) : f.label}</label>
                                                            {f.select ? (
                                                                <select
                                                                    value={value || f.select[0]}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary"
                                                                >
                                                                    {f.select.map(o => <option key={o} value={o}>{o}</option>)}
                                                                </select>
                                                            ) : f.textarea ? (
                                                                <textarea
                                                                    value={value}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    placeholder={f.placeholderKey ? t(f.placeholderKey) : f.placeholder}
                                                                    className={`w-full min-h-[100px] rounded-lg border bg-bg-primary px-3 py-2 text-sm font-mono text-[13px] placeholder:font-sans placeholder:text-sm ${isError ? 'border-danger/60' : 'border-border'}`}
                                                                />
                                                            ) : (
                                                                <input
                                                                    type={f.type || 'text'}
                                                                    value={value}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    placeholder={f.placeholder}
                                                                    className={`w-full min-w-0 h-10 px-3 text-sm rounded-lg border bg-bg-primary ${isError ? 'border-danger/60' : 'border-border'}`}
                                                                    autoComplete="off"
                                                                    spellCheck={false}
                                                                />
                                                            )}
                                                            {hintText && (
                                                                <p className={`mt-1 text-xs ${isError ? 'text-danger' : 'text-amber-300'}`}>{hintText}</p>
                                                            )}
                                                        </div>
                                                    )
                                                })}
                                            </div>
                                        </div>
                                    )
                                })}

                                {/* "+ Weiteren Wert" für Multi-Value-Typen */}
                                {canMulti && (
                                    <button
                                        type="button"
                                        onClick={() => setDynFieldsList(list => [...list, {}])}
                                        className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-dashed border-border hover:border-accent/60 hover:text-accent-light text-text-muted transition-colors"
                                    >
                                        <Plus className="w-3.5 h-3.5" /> {t('zoneDetail.addValue')}
                                    </button>
                                )}

                                <DnsRecordTypeHint recordType={addType} />
                            </div>

                            <div className="flex justify-between items-center gap-3 pt-2 border-t border-border">
                                <p className="text-xs text-text-muted hidden sm:block">{t('zoneDetail.kbdHint')}</p>
                                <div className="flex justify-end gap-3">
                                    <button type="button" onClick={closeModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">{t('common.cancel')}</button>
                                    <button type="submit" disabled={saving || apexWarning?.kind === 'error'} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                        {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('common.save')}
                                    </button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
