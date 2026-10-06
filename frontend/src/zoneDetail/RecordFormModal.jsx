import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Loader2, AlertCircle, X, Sparkles } from 'lucide-react'
import api from '../api'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import DnsRecordTypeHint from '../components/DnsRecordTypeHint'
import ModalErrorBanner from '../components/ModalErrorBanner'
import TtlInput from '../components/TtlInput'
import { normalizeRecordName } from '../lib/dnsName.js'
import { NAME_ERROR_KEYS, rrsetValueCount, selectOptions } from '../lib/recordContent.js'
import { TTL_MAX, TTL_MIN, isValidTtl, parseTtl } from '../lib/ttl.js'
import {
    FIELD_VALIDATORS, getApexWarning, buildQuickTemplates, RECORD_TYPES, MULTI_VALUE_OK, defaultFieldSet, getRecordDef,
} from './zoneDetailModel'
import { useZoneDetail } from './zoneDetailContext'
import {
    collectExtensions, extensionsAt, initialExtStates, mergeRequestBody, notifyExtensions, validateExtensions,
} from './formExtensions.js'

// Record-Dialog (Anlegen / Bearbeiten / Klonen / Schnellvorlage).
// Wird von der Shell bei jedem Oeffnen neu gemountet (key = request.seq), d. h. jeder Aufruf startet frisch.
// Erweiterungen (form-extensions/*.ext.jsx) laufen ueber den Vertrag aus formExtensions.js / form-extensions/README.md.
// F8 (Welle 1): TTL-Feld mit Grenzen (F01/F02), CAA-Defaults (F03), unbekannte Typen als RDATA-Editor (F04),
// Namensnormalisierung (F05), disabled-Flag (F07), Validatoren mit Wert-Set (F10), Wert-Index im Fehler (F11).
// F15 (Welle 3): Typ-Option LUA nur mit LUA-Schreibrecht (ctx.luaPolicy.can_write, sonst disabled mit Zusatz
// "(nur Admins)"/"(deaktiviert)"); Speichern bricht dann mit dem Policy-Text ab (Kosmetik, das Backend prueft).
// Editor-Zusaetze fuer LUA liefert form-extensions/lua.ext.jsx.

// Felder, die Erweiterungen ueber setForm aendern duerfen ('disabled' ist ein Kernfeld, F07)
const FORM_PATCH_KEYS = ['type', 'name', 'ttl', 'fieldsList']

function parseContent(type, content, { warn = false } = {}) {
    const def = getRecordDef(type)
    try {
        return { ...defaultFieldSet(def), ...(def.parse ? def.parse(content) : {}) }
    } catch (e) {
        if (warn) console.warn('Could not parse record content:', e)
        return defaultFieldSet(def)
    }
}

/** TTL eines bestehenden Records: unter dem Minimum wird mit TTL_MIN vorbelegt (Hinweis ttlBelowMin, F8-F01). */
function initialTtl(record) {
    const n = Number(record?.ttl)
    if (Number.isFinite(n) && n < TTL_MIN) return { ttl: String(TTL_MIN), ttlBelowMin: n }
    return { ttl: record?.ttl === undefined || record?.ttl === null ? '3600' : String(record.ttl), ttlBelowMin: null }
}

/** Startzustand aus der Anfrage { mode: 'add'|'edit'|'clone', record } (2.4.1: openAddModal/openEdit/openClone). */
function initialForm(request, relativeName) {
    const r = request?.record
    if (request?.mode === 'edit' && r) {
        return {
            mode: 'edit', isEdit: true, type: r.type, name: relativeName(r.name), ...initialTtl(r),
            fieldsList: [parseContent(r.type, r.content, { warn: true })], oldContent: r.content, disabled: !!r.disabled,
        }
    }
    if (request?.mode === 'clone' && r) {
        return {
            mode: 'clone', isEdit: false, type: r.type, name: relativeName(r.name), ...initialTtl(r),
            fieldsList: [parseContent(r.type, r.content)], oldContent: '', disabled: !!r.disabled,
        }
    }
    return {
        mode: 'add', isEdit: false, type: 'A', name: '@', ttl: '3600', ttlBelowMin: null,
        fieldsList: [defaultFieldSet(getRecordDef('A'))], oldContent: '', disabled: false,
    }
}

/** Werte so weit wie moeglich bauen (fuer Erweiterungen, z. B. PTR-Vorschau); unvollstaendige Sets -> ''. */
function tryBuildContents(def, fieldsList) {
    return fieldsList.map((set) => {
        if (!def) return ''
        try {
            return def.build(set || {})
        } catch {
            return ''
        }
    })
}

export default function RecordFormModal({ request }) {
    const { t } = useTranslation()
    const ctx = useZoneDetail()
    const {
        server, zoneId, zoneName, zoneKey, zoneMeta, records, canEdit, slots,
        closeRecordForm, reportFanout, setSuccess, loadZone, relativeName, luaPolicy,
    } = ctx
    const exts = useMemo(() => slots?.formExtensions || [], [slots])
    const record = request?.record || null

    const zoneInfo = useMemo(
        () => ({ id: zoneId, name: zoneName, key: zoneKey, meta: zoneMeta }),
        [zoneId, zoneName, zoneKey, zoneMeta],
    )

    const [form, setFormState] = useState(() => initialForm(request, relativeName))
    const [extStates, setExtStates] = useState(() => initialExtStates(exts, {
        record, mode: request?.mode || 'add', type: record?.type || 'A', zone: zoneInfo,
    }))
    const [modalError, setModalError] = useState('')
    const [saving, setSaving] = useState(false)
    const formRef = useRef(null)

    const {
        type: addType, name: addName, ttl: addTTL, fieldsList: dynFieldsList, isEdit, oldContent,
        disabled: addDisabled, ttlBelowMin,
    } = form
    const setAddName = (v) => setFormState((f) => ({ ...f, name: v }))
    const setAddTTL = (v) => setFormState((f) => ({ ...f, ttl: v }))
    const setAddDisabled = (v) => setFormState((f) => ({ ...f, disabled: !!v }))
    const setDynFieldsList = (v) => setFormState((f) => ({ ...f, fieldsList: typeof v === 'function' ? v(f.fieldsList) : v }))

    /** Fuer Erweiterungen: Patch-Objekt oder (form) => Patch; erlaubt sind type, name, ttl, fieldsList. */
    const setForm = useCallback((patch) => {
        setFormState((prev) => {
            const p = typeof patch === 'function' ? patch(prev) : patch
            if (!p || typeof p !== 'object') return prev
            const next = { ...prev }
            for (const key of FORM_PATCH_KEYS) if (key in p) next[key] = p[key]
            return next
        })
    }, [])

    const extSetters = useMemo(() => Object.fromEntries(exts.map((ext) => [
        ext.id,
        (value) => setExtStates((prev) => ({ ...prev, [ext.id]: typeof value === 'function' ? value(prev[ext.id]) : value })),
    ])), [exts])

    const closeModal = closeRecordForm
    const quickTemplates = useMemo(() => buildQuickTemplates(zoneName), [zoneName])

    /** Schnellvorlage einfügen */
    function applyQuickTemplate(tpl) {
        setFormState({
            mode: 'template', isEdit: false, oldContent: '', disabled: false, ttlBelowMin: null,
            type: tpl.type, name: tpl.name, ttl: tpl.ttl,
            fieldsList: [{ ...defaultFieldSet(getRecordDef(tpl.type)), ...tpl.fields }],
        })
        setModalError('')
        setExtStates(initialExtStates(exts, { record: null, mode: 'template', type: tpl.type, zone: zoneInfo }))
    }

    /** ESC schließt Modal, Strg/Cmd+Enter speichert */
    useEffect(() => {
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
    }, [saving, closeModal])

    /* ----- Name (F8-F05) -----------------------------------------------------*/
    // Bearbeiten: der Name ist fest, gesendet wird genau der bestehende Record-Name. Sonst: getrimmt, klein,
    // leer = '@', relativ oder als FQDN innerhalb der Zone; Fehler sperren das Speichern.
    const nameResult = useMemo(() => {
        if (isEdit && record?.name) return { fqdn: record.name.endsWith('.') ? record.name : `${record.name}.` }
        return normalizeRecordName(addName, zoneName)
    }, [isEdit, record, addName, zoneName])
    const fqdn = nameResult.fqdn || ''
    const nameError = nameResult.error
        ? t(NAME_ERROR_KEYS[nameResult.error] || 'zoneDetail.invalidHostname', { zone: zoneName })
        : ''
    const isApex = !!fqdn && fqdn.toLowerCase() === zoneKey

    /* ----- Live-Validierung -------------------------------------------------*/
    const apexWarning = getApexWarning(isApex ? '@' : addName, addType)

    /** Validatoren für das aktuell ausgewählte Type – pro Field; bekommen das ganze Wert-Set mit. */
    const validators = FIELD_VALIDATORS[addType] || {}

    function fieldHint(fieldId, value, set) {
        const v = validators[fieldId]
        if (!v) return ''
        return v(value, set)
    }

    /** Existiert ein Record mit (name,type,content) bereits? Name ohne Gross-/Kleinschreibung. */
    function findDuplicate(type, content) {
        const key = fqdn.toLowerCase()
        return records.find(r => r.name.toLowerCase() === key && r.type === type && r.content === content)
    }

    const def = getRecordDef(addType)
    const ttlValid = isValidTtl(addTTL)
    // Die TTL gilt fuer das ganze RRset (F8-F02): Hinweis, sobald das RRset weitere Werte hat bzw. bekommt
    const rrsetCount = fqdn ? rrsetValueCount(records, fqdn, addType) : 0
    const showRrsetNote = isEdit ? rrsetCount > 1 : rrsetCount >= 1

    /** Sicht der Erweiterungen auf das Formular (Props `form`, Argument von validate/collect/onResult). */
    const publicForm = useMemo(() => ({
        mode: form.mode,
        isEdit: form.isEdit,
        type: form.type,
        name: form.name,
        fqdn,
        ttl: form.ttl,
        fieldsList: form.fieldsList,
        contents: tryBuildContents(getRecordDef(form.type), form.fieldsList),
        oldContent: form.oldContent,
        disabled: !!form.disabled,
    }), [form, fqdn])

    /* ----- LUA-Policy (F15) ---------------------------------------------------*/
    const luaWriteOk = luaPolicy?.can_write === true
    const luaDisabledPolicy = luaPolicy?.policy === 'disabled'
    const luaDeniedText = luaPolicy?.reason || t(luaDisabledPolicy ? 'lua.notAllowedDisabled' : 'lua.notAllowedAdmin')
    const luaOptionSuffix = t(luaDisabledPolicy ? 'lua.typeOptionDisabled' : 'lua.typeOptionAdminOnly')

    /* ----- Submit -----------------------------------------------------------*/
    async function handleAddRecord(e) {
        e.preventDefault()
        if (saving) return
        setSaving(true)
        setModalError('')

        if (nameError) {
            setModalError(nameError)
            setSaving(false)
            return
        }

        if (apexWarning?.kind === 'error') {
            setModalError(apexWarning.text)
            setSaving(false)
            return
        }

        // LUA-Policy (F15 6.6): ohne Schreibrecht gar nicht erst senden (Backend antwortet sonst mit 403)
        if (addType === 'LUA' && !luaWriteOk) {
            setModalError(luaDeniedText)
            setSaving(false)
            return
        }

        // TTL 60..604800 (F8-F01); ungueltig sperrt das Speichern
        if (!ttlValid) {
            setModalError(t('ttlInput.outOfRange', { min: TTL_MIN, max: TTL_MAX }))
            setSaving(false)
            return
        }

        // Hartfehler in Feldern blocken den Submit
        for (let i = 0; i < dynFieldsList.length; i++) {
            const set = dynFieldsList[i] || {}
            for (const f of def.fields) {
                const val = set[f.id]
                const lbl = f.labelKey ? t(f.labelKey) : (f.label || f.id)
                if (val === undefined || String(val).trim() === '') {
                    setModalError(t('zoneDetail.fillField', { label: lbl }))
                    setSaving(false)
                    return
                }
                const hint = fieldHint(f.id, val, set)
                if (hint && typeof hint === 'object' && hint.error) {
                    setModalError(dynFieldsList.length > 1
                        ? t('zoneDetail.valueIndexError', { label: lbl, n: i + 1, error: hint.error })
                        : `${lbl}: ${hint.error}`)
                    setSaving(false)
                    return
                }
            }
        }

        // Werte bauen
        const contents = []
        for (const set of dynFieldsList) {
            try { contents.push(def.build(set)) } catch (err) {
                setModalError(t('zoneDetail.valueBuildFailed', { message: err.message || String(err) }))
                setSaving(false)
                return
            }
        }

        // Duplikat-Check (nur Add, nicht beim Edit eines bestehenden Eintrags)
        if (!isEdit) {
            for (const c of contents) {
                const dup = findDuplicate(addType, c)
                if (dup) {
                    setModalError(t('zoneDetail.duplicateRecord', {
                        type: addType,
                        name: dup.name.replace(/\.$/, ''),
                        value: c,
                    }))
                    setSaving(false)
                    return
                }
            }
        }

        // Erweiterungen: validate -> collect (Teil-Body wird gemergt, Kernfelder gewinnen)
        const extForm = { ...publicForm, fqdn, contents }
        const extError = validateExtensions(exts, extStates, extForm)
        if (extError) {
            setModalError(extError.text || t(extError.key, { ...extError.values, defaultValue: extError.key }))
            setSaving(false)
            return
        }
        let extBody
        try {
            extBody = collectExtensions(exts, extStates, extForm).body
        } catch (err) {
            setModalError(err.message || String(err))
            setSaving(false)
            return
        }

        try {
            let res
            if (isEdit) {
                res = await api.updateRecord(server, zoneId, mergeRequestBody({
                    name: fqdn,
                    type: addType,
                    ttl: parseTtl(addTTL),
                    old_content: oldContent,
                    new_content: contents[0],
                    disabled: !!addDisabled,
                }, extBody))
            } else {
                res = await api.createRecord(server, zoneId, mergeRequestBody({
                    name: fqdn,
                    type: addType,
                    ttl: parseTtl(addTTL),
                    records: contents.map(c => ({ content: c, disabled: !!addDisabled })),
                }, extBody))
            }
            closeModal()
            // Fan-out auf Peer-Server: Teilfehler nicht verschlucken (Primary war ok, Peer nicht).
            reportFanout(res?.details)
            const displayName = fqdn.replace(/\.$/, '')
            setSuccess(isEdit
                ? t('zoneDetail.recordUpdated', { type: addType, name: displayName })
                : t('zoneDetail.recordCreated', { type: addType, name: displayName }))
            notifyExtensions(exts, extStates, res, extForm, ctx)
            loadZone()
        } catch (err) {
            setModalError(err.message)
        } finally {
            setSaving(false)
        }
    }

    const canMulti = !isEdit && MULTI_VALUE_OK.has(addType)
    // Typ-Auswahl: bekannte Typen; ein unbekannter Typ (nur beim Bearbeiten moeglich) erscheint zusaetzlich (F8-F04)
    const typeOptions = ALL_RECORD_TYPE_KEYS.filter((k) => RECORD_TYPES[k])
    if (addType && !typeOptions.includes(addType)) typeOptions.unshift(addType)

    function renderExtensions(position) {
        return extensionsAt(exts, addType, position)
            .filter((ext) => ext.Component)
            .map((ext) => {
                const Ext = ext.Component
                return (
                    <Ext
                        key={ext.id}
                        type={addType}
                        form={publicForm}
                        setForm={setForm}
                        record={record}
                        zone={zoneInfo}
                        server={server}
                        isEdit={isEdit}
                        canEdit={canEdit}
                        extState={extStates[ext.id]}
                        setExtState={extSetters[ext.id]}
                    />
                )
            })
    }

    return (
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
                                    disabled={!canEdit}
                                    className="text-xs px-2.5 py-1 rounded-md border border-border bg-bg-primary hover:bg-bg-hover transition-colors disabled:opacity-35 disabled:pointer-events-none"
                                    title={tpl.note || tpl.label}
                                >
                                    {tpl.label}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Modal-weiter Fehler (Submit / Backend) */}
                <ModalErrorBanner
                    message={modalError}
                    title={isEdit ? t('zoneDetail.updateErrorTitle') : t('zoneDetail.createErrorTitle')}
                    onClose={() => setModalError('')}
                />

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
                            <select
                                value={addType}
                                disabled={isEdit}
                                onChange={e => {
                                    const newType = e.target.value
                                    setFormState(f => ({ ...f, type: newType, fieldsList: [defaultFieldSet(getRecordDef(newType))] }))
                                }}
                                className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
                            >
                                {typeOptions.map((k) => {
                                    const v = getRecordDef(k)
                                    const label = v.labelKey ? t(v.labelKey) : v.label
                                    // LUA ohne Schreibrecht: Option gesperrt (ausser beim Bearbeiten eines LUA-Records)
                                    const luaLocked = k === 'LUA' && !luaWriteOk && !(isEdit && addType === 'LUA')
                                    return (
                                        <option key={k} value={k} disabled={luaLocked}>
                                            {luaLocked ? `${label} ${luaOptionSuffix}` : label}
                                        </option>
                                    )
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
                            <div className={`rounded-lg border px-2.5 py-2 ${nameError ? 'border-danger/40 bg-danger/5' : 'border-accent/25 bg-accent/5'}`}>
                                <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted mb-0.5">{t('zoneDetail.namePreviewLabel')}</p>
                                {nameError ? (
                                    <p className="text-xs text-danger break-words" role="alert">{nameError}</p>
                                ) : (
                                    <p className="font-mono text-sm text-accent-light break-all" title={fqdn}>{fqdn}</p>
                                )}
                            </div>
                            <p className="text-xs text-text-muted leading-snug">{t('zoneDetail.mainDomainHint')}</p>
                        </div>
                        <div className="min-w-0 flex flex-col gap-1">
                            {renderExtensions('beforeTtl')}
                            <label htmlFor="record-ttl" className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.ttl')}</label>
                            <TtlInput id="record-ttl" value={addTTL} onChange={setAddTTL} />
                            {ttlBelowMin !== null && ttlBelowMin !== undefined && (
                                <p className="text-xs text-amber-300 leading-snug">{t('zoneDetail.ttlBelowMinimum', { ttl: ttlBelowMin })}</p>
                            )}
                            {showRrsetNote && (
                                <p className="text-xs text-text-muted leading-snug">
                                    {t('zoneDetail.ttlRrsetNote', { name: fqdn.replace(/\.$/, ''), type: addType })}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Dynamische Werte – ggf. mehrere für Round-Robin */}
                    <div className="border-t border-border pt-4 space-y-4">
                        {def.unknown && (
                            <p className="text-xs text-amber-300 leading-snug">{t('zoneDetail.unknownTypeEditHint', { type: addType })}</p>
                        )}
                        {dynFieldsList.map((set, idx) => {
                            const fldList = def.fields || []
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
                                            const hint = fieldHint(f.id, value, set)
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
                                                            {selectOptions(f.select, value).map(o => <option key={o} value={o}>{o}</option>)}
                                                        </select>
                                                    ) : f.textarea ? (
                                                        <textarea
                                                            value={value}
                                                            onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                            placeholder={f.placeholderKey ? t(f.placeholderKey, { defaultValue: '' }) : f.placeholder}
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
                                onClick={() => setDynFieldsList(list => [...list, defaultFieldSet(def)])}
                                className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-dashed border-border hover:border-accent/60 hover:text-accent-light text-text-muted transition-colors"
                            >
                                <Plus className="w-3.5 h-3.5" /> {t('zoneDetail.addValue')}
                            </button>
                        )}

                        {/* Deaktiviert (F8-F07): Zustand wird beim Bearbeiten/Klonen uebernommen */}
                        <label className="flex items-start gap-2 cursor-pointer text-sm text-text-secondary">
                            <input
                                type="checkbox"
                                checked={!!addDisabled}
                                disabled={!canEdit}
                                onChange={(e) => setAddDisabled(e.target.checked)}
                                className="w-4 h-4 rounded mt-0.5"
                            />
                            <span>{t('zoneDetail.disabledLabel')}</span>
                        </label>

                        {renderExtensions('afterValues')}

                        <DnsRecordTypeHint recordType={addType} />
                    </div>

                    {renderExtensions('footer')}

                    <div className="flex justify-between items-center gap-3 pt-2 border-t border-border">
                        <p className="text-xs text-text-muted hidden sm:block">{t('zoneDetail.kbdHint')}</p>
                        <div className="flex justify-end gap-3">
                            <button type="button" onClick={closeModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">{t('common.cancel')}</button>
                            <button type="submit" disabled={!canEdit || saving || apexWarning?.kind === 'error' || !!nameError} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('common.save')}
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        </div>
    )
}
