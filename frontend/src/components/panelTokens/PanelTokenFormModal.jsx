import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Info, KeyRound, Loader2, Plus, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import { useDateFormat } from '../../lib/useDateFormat'
import { useDialogFocus } from '../../lib/useDialogFocus'
import {
    EXPIRY_OPTIONS, NAME_MAX,
    buildCreatePayload, buildUpdatePayload, filterZoneOptions, initialForm, isValidZone, mergeZoneNames,
    normalizeZoneInput, validateForm, zoneOptionsFromPermissions,
} from '../../lib/panelTokens'
import ModalPortal from '../common/ModalPortal'

// Admin: Zonen aller erreichbaren Server laden (F14 §2.3). Liefert { names, failed: [Servername] }.
async function loadAdminZones() {
    const res = await api.getServers()
    const servers = Array.isArray(res?.servers) ? res.servers : []
    const failed = servers.filter((s) => s.is_reachable === false).map((s) => s.name)
    const reachable = servers.filter((s) => s.is_reachable !== false)
    const results = await Promise.allSettled(reachable.map((s) => api.listZones(encodeURIComponent(s.name))))
    const lists = []
    results.forEach((r, i) => {
        if (r.status === 'fulfilled') lists.push((r.value?.zones || []).map((z) => z.name))
        else failed.push(reachable[i].name)
    })
    return { names: mergeZoneNames(...lists), failed }
}

// Erstellen/Bearbeiten eines Panel-API-Tokens (F14 §2.2-2.4, §6.4).
// Props: { mode: 'create'|'edit', token, isAdmin, zonePermissions, onClose, onSaved(result) }
//   onSaved({ mode: 'create', token, plaintext_token }) bzw. onSaved({ mode: 'edit', token, message })
// Fehler erscheinen nur im Modal. Beim Bearbeiten werden nur geaenderte Felder gesendet; ohne Aenderung
// schliesst das Modal ohne Request. Ein Token wird durch Bearbeiten nie neu erzeugt.
export default function PanelTokenFormModal({ mode, token, isAdmin, zonePermissions, onClose, onSaved }) {
    const { t } = useTranslation()
    const { fmtDate } = useDateFormat()
    const isEdit = mode === 'edit'
    const titleId = useId()
    const formRef = useRef(null)
    const nameRef = useRef(null)
    const loadedRef = useRef(false)
    // Ohne eigene Zonen bleibt nur "Alle meine Zonen" (Token ohne Zonenzugriff, z. B. fuer /auth/me, F14 2.2 Nr. 3)
    const [form, setForm] = useState(() => {
        const initial = initialForm(mode, token)
        const ownZones = Object.keys(zonePermissions || {}).length
        return mode !== 'edit' && !isAdmin && ownZones === 0 ? { ...initial, scopeMode: 'all' } : initial
    })
    const [filter, setFilter] = useState('')
    const [manualZone, setManualZone] = useState('')
    const [manualError, setManualError] = useState('')
    const [adminZones, setAdminZones] = useState([])
    const [zonesLoading, setZonesLoading] = useState(false)
    const [zonesPartial, setZonesPartial] = useState([])
    const [zonesFailed, setZonesFailed] = useState(false)
    const [modalError, setModalError] = useState('')
    const [busy, setBusy] = useState(false)

    const userOptions = useMemo(() => zoneOptionsFromPermissions(zonePermissions), [zonePermissions])
    const noOwnZones = !isAdmin && userOptions.length === 0

    // Optionen = Vereinigung aus verfuegbaren Zonen und bereits ausgewaehlten (z. B. nicht mehr zugaengliche)
    const options = useMemo(() => {
        const base = isAdmin ? adminZones.map((name) => ({ name, readOnly: false })) : userOptions
        const known = new Set(base.map((o) => o.name))
        const extra = form.zones.filter((z) => !known.has(z)).map((name) => ({ name, readOnly: false, extra: true }))
        return [...base, ...extra].sort((a, b) => a.name.localeCompare(b.name))
    }, [isAdmin, adminZones, userOptions, form.zones])
    const { shown, rest } = filterZoneOptions(options, filter)
    const selected = useMemo(() => new Set(form.zones), [form.zones])

    function ensureAdminZones() {
        if (!isAdmin || loadedRef.current) return
        loadedRef.current = true
        setZonesLoading(true)
        loadAdminZones()
            .then(({ names, failed }) => {
                setAdminZones(names)
                setZonesPartial(failed)
            })
            .catch(() => setZonesFailed(true))
            .finally(() => setZonesLoading(false))
    }

    // Bearbeiten mit Zonen-Scope: Admin-Zonenliste gleich laden (Ereignis ausserhalb des Renders)
    useEffect(() => {
        if (isEdit && form.scopeMode === 'selected') queueMicrotask(() => ensureAdminZones())
        // nur beim Oeffnen
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const dialogRef = useDialogFocus({ onClose, canClose: !busy, initialFocusRef: nameRef })

    // Strg/Cmd+Enter sendet ab (Fokus, Tab-Falle, ESC und Fokus-Rueckgabe: lib/useDialogFocus).
    useEffect(() => {
        function onKey(e) {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !busy) {
                e.preventDefault()
                formRef.current?.requestSubmit()
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [busy])

    function setField(key, value) {
        setForm((f) => ({ ...f, [key]: value }))
    }

    function setScopeMode(value) {
        // Admin-Freigabe nur ohne Zonen-Scope (Backend erzwingt dasselbe)
        setForm((f) => ({ ...f, scopeMode: value, allowAdmin: value === 'selected' ? false : f.allowAdmin }))
        if (value === 'selected') ensureAdminZones()
    }

    function toggleZone(name) {
        setForm((f) => ({
            ...f,
            zones: f.zones.includes(name) ? f.zones.filter((z) => z !== name) : mergeZoneNames(f.zones, [name]),
        }))
    }

    function addManualZone() {
        const n = normalizeZoneInput(manualZone)
        if (!isValidZone(n)) {
            setManualError(t('panelTokens.zoneInvalid'))
            return
        }
        setManualError('')
        setManualZone('')
        setForm((f) => ({ ...f, zones: mergeZoneNames(f.zones, [n]) }))
    }

    const errKey = validateForm(form)

    async function handleSubmit(e) {
        e.preventDefault()
        if (busy) return
        setModalError('')
        if (errKey) {
            setModalError(t(errKey))
            return
        }
        let body
        if (isEdit) {
            body = buildUpdatePayload(token, form)
            if (Object.keys(body).length === 0) {
                onClose()
                return
            }
        } else {
            body = buildCreatePayload(form)
        }
        setBusy(true)
        try {
            const res = isEdit ? await api.updatePanelToken(token.id, body) : await api.createPanelToken(body)
            onSaved({ mode, ...res })
        } catch (err) {
            setModalError(err.message)
            setBusy(false)
        }
    }

    const expiryCurrent = token?.expires_at ? fmtDate(token.expires_at) : t('panelTokens.expiryNever')
    const radioCls = 'mt-0.5'
    const hintCls = 'block text-[11px] text-text-muted'

    return (
        <ModalPortal>
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby={titleId}
                    className="glass-card w-full max-w-2xl max-h-[90vh] flex flex-col"
                >
                    <div className="flex items-center justify-between gap-3 px-6 pt-5 pb-3 border-b border-border/50">
                        <h2 id={titleId} className="text-lg font-bold flex items-center gap-2 text-text-primary">
                            <KeyRound className="w-5 h-5" aria-hidden="true" />
                            {isEdit ? t('panelTokens.modalEditTitle') : t('panelTokens.modalCreateTitle')}
                        </h2>
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={busy}
                            className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-40"
                            aria-label={t('common.close')}
                            title={t('common.close')}
                        >
                            <X className="w-5 h-5" aria-hidden="true" />
                        </button>
                    </div>

                    <form ref={formRef} onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-4 space-y-5" noValidate>
                        <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />

                        {/* Bezeichnung */}
                        <div>
                            <label htmlFor={`${titleId}-name`} className="block text-xs font-medium text-text-secondary mb-0.5">
                                {t('panelTokens.fieldName')}
                            </label>
                            <p className="text-[11px] text-text-muted mb-1">{t('panelTokens.fieldNameHint')}</p>
                            <input
                                id={`${titleId}-name`}
                                ref={nameRef}
                                value={form.name}
                                maxLength={NAME_MAX}
                                onChange={(e) => setField('name', e.target.value)}
                                className="w-full px-3 py-2 text-sm"
                                autoComplete="off"
                            />
                        </div>

                        {/* Gueltig fuer */}
                        <fieldset className="space-y-2">
                            <legend className="block text-xs font-medium text-text-secondary mb-1">{t('panelTokens.fieldZones')}</legend>
                            <label className="flex items-start gap-2 text-sm cursor-pointer">
                                <input
                                    type="radio"
                                    name={`${titleId}-scope`}
                                    className={radioCls}
                                    checked={form.scopeMode === 'all'}
                                    onChange={() => setScopeMode('all')}
                                />
                                <span>
                                    {isAdmin ? t('panelTokens.zonesAll') : t('panelTokens.zonesAllMine')}
                                    <span className={hintCls}>{isAdmin ? t('panelTokens.zonesAllAdminHint') : t('panelTokens.zonesAllHint')}</span>
                                </span>
                            </label>
                            <label className={`flex items-start gap-2 text-sm ${noOwnZones ? 'opacity-50' : 'cursor-pointer'}`}>
                                <input
                                    type="radio"
                                    name={`${titleId}-scope`}
                                    className={radioCls}
                                    checked={form.scopeMode === 'selected'}
                                    disabled={noOwnZones && form.scopeMode !== 'selected'}
                                    onChange={() => setScopeMode('selected')}
                                />
                                <span>{t('panelTokens.zonesSelected')}</span>
                            </label>
                            {noOwnZones && (
                                <p className="flex items-start gap-2 p-2 rounded-lg bg-sky-500/10 border border-sky-500/30 text-xs text-sky-200">
                                    <Info className="w-4 h-4 shrink-0" aria-hidden="true" />
                                    {t('panelTokens.zonesNoneAvailable')}
                                </p>
                            )}

                            {form.scopeMode === 'selected' && (
                                <div className="pl-6 space-y-2">
                                    {form.zones.length > 0 && (
                                        <ul className="flex flex-wrap gap-1.5" aria-label={t('panelTokens.zoneCount', { count: form.zones.length })}>
                                            {form.zones.map((z) => (
                                                <li key={z} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-accent/15 border border-accent/30 text-xs font-mono">
                                                    {z}
                                                    <button
                                                        type="button"
                                                        onClick={() => toggleZone(z)}
                                                        className="hover:text-danger"
                                                        aria-label={`${t('common.delete')}: ${z}`}
                                                        title={t('common.delete')}
                                                    >
                                                        <X className="w-3 h-3" aria-hidden="true" />
                                                    </button>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                    <p className="text-xs text-text-muted">{t('panelTokens.zoneCount', { count: form.zones.length })}</p>
                                    <input
                                        type="search"
                                        value={filter}
                                        onChange={(e) => setFilter(e.target.value)}
                                        placeholder={t('panelTokens.zonesSearch')}
                                        aria-label={t('panelTokens.zonesSearch')}
                                        className="w-full px-3 py-1.5 text-sm"
                                    />
                                    {zonesLoading && (
                                        <p className="text-xs text-text-muted flex items-center gap-2">
                                            <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> {t('panelTokens.zonesLoading')}
                                        </p>
                                    )}
                                    {zonesFailed && (
                                        <p className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200">
                                            <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                                            {t('panelTokens.zonesLoadFailed')}
                                        </p>
                                    )}
                                    {zonesPartial.length > 0 && (
                                        <p className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200">
                                            <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                                            {t('panelTokens.zonesLoadPartial', { servers: zonesPartial.join(', ') })}
                                        </p>
                                    )}
                                    <div className="max-h-64 overflow-y-auto rounded-lg border border-border/60 divide-y divide-border/40">
                                        {shown.map((o) => (
                                            <label key={o.name} className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-bg-hover">
                                                <input type="checkbox" checked={selected.has(o.name)} onChange={() => toggleZone(o.name)} />
                                                <span className="font-mono text-xs break-all flex-1">{o.name}</span>
                                                {o.readOnly && (
                                                    <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-border text-text-muted">
                                                        {t('panelTokens.zoneReadOnlyBadge')}
                                                    </span>
                                                )}
                                            </label>
                                        ))}
                                        {!zonesLoading && shown.length === 0 && (
                                            <p className="px-3 py-2 text-xs text-text-muted">–</p>
                                        )}
                                    </div>
                                    {rest > 0 && <p className="text-xs text-amber-300">{t('panelTokens.zonesTooMany', { count: rest })}</p>}
                                    {isAdmin && (
                                        <div>
                                            <label htmlFor={`${titleId}-manual`} className="block text-xs text-text-secondary mb-0.5">
                                                {t('panelTokens.zoneAddManual')}
                                            </label>
                                            <div className="flex gap-2">
                                                <input
                                                    id={`${titleId}-manual`}
                                                    value={manualZone}
                                                    onChange={(e) => { setManualZone(e.target.value); setManualError('') }}
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
                                                            e.preventDefault()
                                                            addManualZone()
                                                        }
                                                    }}
                                                    placeholder={t('panelTokens.zoneAddPlaceholder')}
                                                    className="flex-1 px-3 py-1.5 text-sm font-mono"
                                                    autoComplete="off"
                                                    aria-invalid={!!manualError}
                                                />
                                                <button
                                                    type="button"
                                                    onClick={addManualZone}
                                                    className="px-3 py-1.5 rounded-lg bg-accent/20 text-sm inline-flex items-center gap-1"
                                                >
                                                    <Plus className="w-4 h-4" aria-hidden="true" /> {t('panelTokens.zoneAddButton')}
                                                </button>
                                            </div>
                                            {manualError && <p className="text-xs text-danger mt-1">{manualError}</p>}
                                        </div>
                                    )}
                                </div>
                            )}
                        </fieldset>

                        {/* Berechtigung */}
                        <fieldset className="space-y-2">
                            <legend className="block text-xs font-medium text-text-secondary mb-1">{t('panelTokens.fieldPermission')}</legend>
                            {[['manage', 'permManage', 'permManageHint'], ['read', 'permRead', 'permReadHint']].map(([value, label, hint]) => (
                                <label key={value} className="flex items-start gap-2 text-sm cursor-pointer">
                                    <input
                                        type="radio"
                                        name={`${titleId}-perm`}
                                        className={radioCls}
                                        checked={form.permission === value}
                                        onChange={() => setField('permission', value)}
                                    />
                                    <span>
                                        {t(`panelTokens.${label}`)}
                                        <span className={hintCls}>{t(`panelTokens.${hint}`)}</span>
                                    </span>
                                </label>
                            ))}
                        </fieldset>

                        {/* Ablauf */}
                        <div>
                            <label htmlFor={`${titleId}-expiry`} className="block text-xs font-medium text-text-secondary mb-1">
                                {t('panelTokens.expiry')}
                            </label>
                            <select
                                id={`${titleId}-expiry`}
                                value={form.expiry}
                                onChange={(e) => setField('expiry', e.target.value)}
                                className="w-full sm:w-auto px-3 py-2 text-sm"
                            >
                                {isEdit && (
                                    <option value="unchanged">{t('panelTokens.expiryUnchanged', { value: expiryCurrent })}</option>
                                )}
                                {EXPIRY_OPTIONS.map((d) => (
                                    <option key={d} value={String(d)}>{t('panelTokens.expiryDays', { count: d })}</option>
                                ))}
                                <option value="never">{t('panelTokens.expiryNever')}</option>
                            </select>
                            {isEdit && <p className="text-[11px] text-text-muted mt-1">{t('panelTokens.expiryEditHint')}</p>}
                            {form.expiry === 'never' && (
                                <p className="flex items-start gap-2 mt-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200">
                                    <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                                    {t('panelTokens.expiryNeverWarning')}
                                </p>
                            )}
                        </div>

                        {/* Admin-Freigabe (nur Admins) */}
                        {isAdmin && (
                            <div>
                                <label className={`flex items-start gap-2 text-sm ${form.scopeMode === 'selected' ? 'opacity-50' : 'cursor-pointer'}`}>
                                    <input
                                        type="checkbox"
                                        className="mt-0.5"
                                        checked={form.allowAdmin && form.scopeMode === 'all'}
                                        disabled={form.scopeMode === 'selected'}
                                        onChange={(e) => setField('allowAdmin', e.target.checked)}
                                    />
                                    <span>
                                        {t('panelTokens.allowAdmin')}
                                        <span className={hintCls}>
                                            {form.scopeMode === 'selected' ? t('panelTokens.allowAdminNeedsAllZones') : t('panelTokens.allowAdminHint')}
                                        </span>
                                    </span>
                                </label>
                            </div>
                        )}

                        <p className="flex items-start gap-2 text-xs text-text-muted">
                            <Info className="w-4 h-4 shrink-0" aria-hidden="true" />
                            {t('panelTokens.scopeNotice')}
                        </p>
                    </form>

                    <div className="flex justify-end gap-2 px-6 py-4 border-t border-border/50">
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={busy}
                            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50"
                        >
                            {t('common.cancel')}
                        </button>
                        <button
                            type="button"
                            onClick={() => formRef.current?.requestSubmit()}
                            disabled={busy || !!errKey}
                            className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium inline-flex items-center gap-2 disabled:opacity-50"
                        >
                            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {isEdit ? t('common.save') : t('panelTokens.submitCreate')}
                        </button>
                    </div>
                </div>
            </div>
        </ModalPortal>
    )
}
