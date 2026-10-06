import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, Router, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import { useDialogFocus } from '../../lib/useDialogFocus'
import ModalPortal from '../common/ModalPortal'

// Anlegen/Bearbeiten eines DynDNS-Tokens (F9 §2.1/§2.4).
// Props: { mode: 'create'|'edit', token, zones: [{ name, servers }], limits, onClose, onSaved(result) }
//   limits = { ttlMin, ttlMax, ttlDefault, maxHostnames } (aus GET /dyndns/info, sonst Spec-Defaults)
//   onSaved({ kind: 'created', result }) nach POST (result = { token, plaintext_token, warning }),
//   onSaved({ kind: 'saved', result }) nach PUT, onSaved(null) wenn sich beim Bearbeiten nichts geaendert hat.
// Fehler erscheinen nur im Modal (ModalErrorBanner). ESC schliesst (nicht waehrend des Speicherns), Strg/Cmd+Enter
// speichert; Fokus startet im Namensfeld, bleibt im Dialog (Tab-Falle) und kehrt beim Schliessen zurueck.

const NAME_MAX = 100
const TTL_PRESETS = [60, 300, 3600]
const TYPES = ['A', 'AAAA']
// Ein Label: Buchstaben/Ziffern (auch IDN), Bindestrich nicht am Rand, Unterstrich erlaubt; 1-63 Zeichen.
const LABEL_RE = /^(?!-)[\p{L}\p{N}_-]{1,63}(?<!-)$/u

const stripDot = (s) => String(s || '').trim().replace(/\.$/, '').toLowerCase()

// Relativer Name + Zone -> FQDN ohne Punkt ('' bei ungueltiger Eingabe). '@' bzw. leer = Zone selbst;
// wer den vollen Namen in der Zone eintippt, bekommt ihn unveraendert.
function buildHostname(sub, zone) {
    const z = stripDot(zone)
    if (!z) return ''
    const s = String(sub || '').trim().toLowerCase().replace(/\.+$/, '')
    if (!s || s === '@') return z
    if (s === z || s.endsWith(`.${z}`)) return s
    return `${s}.${z}`
}

function isValidHostname(host) {
    const h = stripDot(host)
    if (!h || h.length > 253 || h.includes('*')) return false
    return h.split('.').every((label) => LABEL_RE.test(label))
}

function initialForm(mode, token, limits) {
    if (mode === 'edit' && token) {
        return {
            name: token.name || '',
            hostnames: (token.hostnames || []).map(stripDot),
            types: TYPES.filter((x) => (token.allowed_types || TYPES).includes(x)),
            ttl: String(token.ttl ?? limits.ttlDefault),
            updatePtr: !!token.update_ptr,
            isActive: token.is_active !== false,
        }
    }
    return { name: '', hostnames: [], types: [...TYPES], ttl: String(limits.ttlDefault), updatePtr: false, isActive: true }
}

function sameList(a, b) {
    return a.length === b.length && a.every((x, i) => x === b[i])
}

export default function DyndnsTokenModal({ mode, token, zones = [], limits: rawLimits, onClose, onSaved }) {
    const { t } = useTranslation()
    const isEdit = mode === 'edit'
    const limits = useMemo(() => ({
        ttlMin: rawLimits?.ttlMin ?? 60,
        ttlMax: rawLimits?.ttlMax ?? 86400,
        ttlDefault: rawLimits?.ttlDefault ?? 60,
        maxHostnames: rawLimits?.maxHostnames ?? 20,
    }), [rawLimits])
    const titleId = useId()
    const zoneListId = useId()
    const ttlListId = useId()
    const formRef = useRef(null)
    const nameRef = useRef(null)
    const [form, setForm] = useState(() => initialForm(mode, token, limits))
    const [zoneInput, setZoneInput] = useState(() => (zones.length === 1 ? stripDot(zones[0].name) : ''))
    const [subInput, setSubInput] = useState('')
    const [hostError, setHostError] = useState('')
    const [modalError, setModalError] = useState('')
    const [saving, setSaving] = useState(false)

    const zoneNames = useMemo(() => zones.map((z) => stripDot(z.name)).filter(Boolean), [zones])
    const zoneKnown = zoneNames.includes(stripDot(zoneInput))
    const preview = zoneKnown ? buildHostname(subInput, zoneInput) : ''

    // Fokus (Namensfeld), Tab-Falle, ESC (nicht waehrend des Speicherns) und Fokus-Rueckgabe: lib/useDialogFocus
    const dialogRef = useDialogFocus({ onClose, canClose: !saving, initialFocusRef: nameRef })

    // Strg/Cmd+Enter speichert
    useEffect(() => {
        function onKey(e) {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !saving) {
                e.preventDefault()
                formRef.current?.requestSubmit()
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [saving])

    function setField(key, value) {
        setForm((f) => ({ ...f, [key]: value }))
    }

    function addHostname() {
        setHostError('')
        if (!zoneKnown) {
            setHostError(t('dyndns.errZoneUnknown'))
            return
        }
        const host = buildHostname(subInput, zoneInput)
        if (!host || !isValidHostname(host)) {
            setHostError(t('dyndns.errHostnameInvalid'))
            return
        }
        if (form.hostnames.includes(host)) {
            setHostError(t('dyndns.errHostnameDuplicate'))
            return
        }
        if (form.hostnames.length >= limits.maxHostnames) {
            setHostError(t('dyndns.errHostnameMax', { max: limits.maxHostnames }))
            return
        }
        setField('hostnames', [...form.hostnames, host])
        setSubInput('')
    }

    function removeHostname(host) {
        setField('hostnames', form.hostnames.filter((h) => h !== host))
    }

    function toggleType(type, on) {
        const next = on ? [...new Set([...form.types, type])] : form.types.filter((x) => x !== type)
        setField('types', TYPES.filter((x) => next.includes(x)))
    }

    function validate() {
        const name = form.name.trim()
        if (!name) return t('dyndns.errNameRequired')
        if (name.length > NAME_MAX) return t('dyndns.errNameTooLong', { max: NAME_MAX })
        if (form.hostnames.length === 0) return t('dyndns.errHostnameRequired')
        if (form.hostnames.length > limits.maxHostnames) return t('dyndns.errHostnameMax', { max: limits.maxHostnames })
        if (form.types.length === 0) return t('dyndns.errTypeRequired')
        const ttl = Number(form.ttl)
        if (!Number.isInteger(ttl) || ttl < limits.ttlMin || ttl > limits.ttlMax) {
            return t('dyndns.errTtlRange', { min: limits.ttlMin, max: limits.ttlMax })
        }
        return ''
    }

    async function handleSubmit(e) {
        e.preventDefault()
        if (saving) return
        setModalError('')
        const err = validate()
        if (err) {
            setModalError(err)
            return
        }
        const payload = {
            name: form.name.trim(),
            hostnames: form.hostnames,
            allowed_types: form.types,
            ttl: Number(form.ttl),
            update_ptr: !!form.updatePtr,
        }
        let body = payload
        if (isEdit) {
            // Nur geaenderte Felder senden (Audit DYNDNS_TOKEN_UPDATE listet genau diese)
            body = {}
            if (payload.name !== token.name) body.name = payload.name
            if (!sameList(payload.hostnames, (token.hostnames || []).map(stripDot))) body.hostnames = payload.hostnames
            if (!sameList(payload.allowed_types, TYPES.filter((x) => (token.allowed_types || []).includes(x)))) {
                body.allowed_types = payload.allowed_types
            }
            if (payload.ttl !== token.ttl) body.ttl = payload.ttl
            if (payload.update_ptr !== !!token.update_ptr) body.update_ptr = payload.update_ptr
            if (form.isActive !== (token.is_active !== false)) body.is_active = form.isActive
            if (Object.keys(body).length === 0) {
                onSaved(null)
                return
            }
        }
        setSaving(true)
        try {
            const result = isEdit
                ? await api.updateDyndnsToken(token.id, body)
                : await api.createDyndnsToken(body)
            onSaved({ kind: isEdit ? 'saved' : 'created', result })
        } catch (e2) {
            setModalError(e2.message)
            setSaving(false)
        }
    }

    const inputCls = 'w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary'

    return (
        <ModalPortal>
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby={titleId}
                    className="glass-card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6"
                >
                    <div className="flex items-center justify-between gap-3 mb-4">
                        <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2">
                            <Router className="w-5 h-5" aria-hidden="true" />
                            {isEdit ? t('dyndns.editToken') : t('dyndns.createToken')}
                        </h2>
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={saving}
                            className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary disabled:opacity-50"
                            aria-label={t('common.close')}
                        >
                            <X className="w-5 h-5" aria-hidden="true" />
                        </button>
                    </div>

                    <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />

                    <form ref={formRef} onSubmit={handleSubmit} className="space-y-4" noValidate>
                        <div>
                            <label htmlFor={`${titleId}-name`} className="block text-xs font-medium text-text-secondary mb-1">{t('dyndns.fieldName')}</label>
                            <input
                                id={`${titleId}-name`}
                                ref={nameRef}
                                value={form.name}
                                maxLength={NAME_MAX}
                                onChange={(e) => setField('name', e.target.value)}
                                placeholder={t('dyndns.fieldNamePlaceholder')}
                                className={inputCls}
                            />
                        </div>

                        <fieldset className="space-y-2">
                            <legend className="block text-xs font-medium text-text-secondary">{t('dyndns.fieldHostnames')}</legend>
                            <p className="text-xs text-text-muted">{t('dyndns.fieldHostnamesHint')}</p>
                            <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 items-end">
                                <div>
                                    <label htmlFor={`${titleId}-zone`} className="block text-xs text-text-muted mb-0.5">{t('dyndns.fieldZone')}</label>
                                    <input
                                        id={`${titleId}-zone`}
                                        list={zoneListId}
                                        value={zoneInput}
                                        onChange={(e) => { setZoneInput(e.target.value); setHostError('') }}
                                        placeholder={t('dyndns.fieldZonePlaceholder')}
                                        autoComplete="off"
                                        className={inputCls}
                                    />
                                    <datalist id={zoneListId}>
                                        {zoneNames.map((z) => <option key={z} value={z} />)}
                                    </datalist>
                                </div>
                                <div>
                                    <label htmlFor={`${titleId}-sub`} className="block text-xs text-text-muted mb-0.5">{t('dyndns.fieldSubdomain')}</label>
                                    <input
                                        id={`${titleId}-sub`}
                                        value={subInput}
                                        onChange={(e) => { setSubInput(e.target.value); setHostError('') }}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
                                                e.preventDefault()
                                                addHostname()
                                            }
                                        }}
                                        placeholder={t('dyndns.fieldSubdomainPlaceholder')}
                                        autoComplete="off"
                                        className={inputCls}
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={addHostname}
                                    className="h-10 px-3 rounded-lg bg-accent/20 text-accent-light text-sm flex items-center justify-center gap-1 hover:bg-accent/30"
                                >
                                    <Plus className="w-4 h-4" aria-hidden="true" /> {t('dyndns.addHostname')}
                                </button>
                            </div>
                            {preview && (
                                <p className="text-xs text-text-muted">
                                    {t('dyndns.hostnamePreview', { fqdn: preview })}
                                </p>
                            )}
                            {hostError && <p role="alert" className="text-xs text-danger">{hostError}</p>}
                            {form.hostnames.length > 0 && (
                                <ul className="flex flex-wrap gap-1.5" aria-label={t('dyndns.fieldHostnames')}>
                                    {form.hostnames.map((h) => (
                                        <li key={h} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full bg-accent/10 border border-accent/30 text-xs font-mono text-text-primary">
                                            <span className="break-all">{h}</span>
                                            <button
                                                type="button"
                                                onClick={() => removeHostname(h)}
                                                className="p-0.5 rounded-full hover:bg-danger/20 hover:text-danger"
                                                aria-label={`${t('dyndns.removeHostname')}: ${h}`}
                                                title={t('dyndns.removeHostname')}
                                            >
                                                <X className="w-3 h-3" aria-hidden="true" />
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </fieldset>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <fieldset>
                                <legend className="block text-xs font-medium text-text-secondary mb-1">{t('dyndns.fieldTypes')}</legend>
                                <div className="flex gap-4">
                                    {TYPES.map((type) => (
                                        <label key={type} className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={form.types.includes(type)}
                                                onChange={(e) => toggleType(type, e.target.checked)}
                                                className="w-4 h-4 rounded"
                                            />
                                            {type}
                                        </label>
                                    ))}
                                </div>
                            </fieldset>
                            <div>
                                <label htmlFor={`${titleId}-ttl`} className="block text-xs font-medium text-text-secondary mb-1">{t('dyndns.fieldTtl')}</label>
                                <input
                                    id={`${titleId}-ttl`}
                                    type="number"
                                    inputMode="numeric"
                                    min={limits.ttlMin}
                                    max={limits.ttlMax}
                                    list={ttlListId}
                                    value={form.ttl}
                                    onChange={(e) => setField('ttl', e.target.value)}
                                    className={inputCls}
                                />
                                <datalist id={ttlListId}>
                                    {TTL_PRESETS.map((v) => <option key={v} value={v} />)}
                                </datalist>
                                <p className="mt-1 text-xs text-text-muted">{t('dyndns.fieldTtlHint', { min: limits.ttlMin, max: limits.ttlMax })}</p>
                            </div>
                        </div>

                        <div className="space-y-1">
                            <label className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={form.updatePtr}
                                    onChange={(e) => setField('updatePtr', e.target.checked)}
                                    className="w-4 h-4 rounded mt-0.5"
                                />
                                <span>{t('dyndns.fieldPtr')}</span>
                            </label>
                            <p className="text-xs text-text-muted pl-6">{t('dyndns.fieldPtrHint')}</p>
                        </div>

                        {isEdit && (
                            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={form.isActive}
                                    onChange={(e) => setField('isActive', e.target.checked)}
                                    className="w-4 h-4 rounded"
                                />
                                {t('dyndns.fieldActive')}
                            </label>
                        )}

                        <div className="flex justify-end gap-3 pt-2 border-t border-border">
                            <button type="button" onClick={onClose} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                                {t('common.cancel')}
                            </button>
                            <button
                                type="submit"
                                disabled={saving}
                                className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                            >
                                {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                                {isEdit ? t('common.save') : t('dyndns.createToken')}
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </ModalPortal>
    )
}
