import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ChevronRight, FileText, Loader2, RefreshCw, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import PtrSyncOption from '../PtrSyncOption'
import TtlInput from '../TtlInput'
import { getDefault, getManagePtr, setManagePtr } from '../../lib/ptrPreference.js'
import BulkPreviewView from './BulkPreviewView'
import {
    TEXT_MODES, TTL_MAX, TTL_MIN, applyErrorIssues, buildTextRequest, canApply, isConflictError, isTextEmpty,
    isValidBulkTtl, issueI18n, lineIssuesOf, lineOffsets, loadAllPrefill, needsLinePrefix, previewChanges,
    refreshRequest, touchesPtrTypes,
} from '../../zoneDetail/bulkModel.js'

// Editor-/Vorschau-Modal des Bulk-Editors (F1 2.4/2.5, 6.3.3). Zwei Schritte: "text" (BIND-Text, Modus, Standard-TTL,
// Zeilenfehler mit Sprung) und "preview" (BulkPreviewView, Pflicht-Checkbox bei Loeschungen, PTR-Option).
// Der Aufrufer mountet das Modal je Oeffnen neu (key = initial.id) – kein Effekt zum Zuruecksetzen.
//
// initial = { flow: 'text'|'selection', step: 'text'|'preview', text, mode, scope, defaultTtl, request, preview,
//             loadedHint: { rrsets, values } | null }
// onApplied(res, { managePtr }) wird nach 200 von POST /bulk aufgerufen (Banner, Auswahl leeren, neu laden).
const SYNTAX_EXAMPLE = [
    '$ORIGIN example.com.',
    '$TTL 3600',
    '@            IN  MX     10 mail.example.com.',
    'www     300  IN  A      192.0.2.10',
    '             IN  AAAA   2001:db8::10',
    'txt          IN  TXT    ( "v=spf1"',
    '                          "-all" )',
    ';@disabled old 300 IN A 192.0.2.99',
].join('\n')

const MODE_LABELS = { merge: 'bulk.modeMerge', replace: 'bulk.modeReplace', sync_scope: 'bulk.modeSync' }
const MODE_HELP = { merge: 'bulk.modeMergeHelp', replace: 'bulk.modeReplaceHelp', sync_scope: 'bulk.modeSyncHelp' }

export default function BulkEditorModal({ server, zoneId, zoneKey, records, initial, onClose, onApplied }) {
    const { t } = useTranslation()
    const titleId = useId()
    const textareaRef = useRef(null)
    const primaryRef = useRef(null)
    const textFlow = initial?.flow === 'text'

    const [step, setStep] = useState(initial?.step || 'text')
    const [initialText] = useState(initial?.text || '')
    const [text, setText] = useState(initial?.text || '')
    const [mode, setMode] = useState(TEXT_MODES.includes(initial?.mode) ? initial.mode : 'merge')
    const [scope, setScope] = useState(Array.isArray(initial?.scope) ? initial.scope : [])
    const [defaultTtl, setDefaultTtl] = useState(String(initial?.defaultTtl ?? 3600))
    const [loadedHint, setLoadedHint] = useState(initial?.loadedHint || null)
    const [lastRequest, setLastRequest] = useState(initial?.request || null)
    const [preview, setPreview] = useState(initial?.preview || null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')
    const [lineIssues, setLineIssues] = useState([])
    const [applyIssues, setApplyIssues] = useState([])
    const [confirmRemoval, setConfirmRemoval] = useState(false)
    const [conflict, setConflict] = useState(false)
    // PTR-Pflege (F11): gemerkte Auswahl dieser Zone/dieses Browsers; null = nichts gemerkt -> Admin-Default
    const [ptrChoice, setPtrChoice] = useState(() => getManagePtr(zoneKey))

    const dirty = textFlow && text !== initialText
    const syncAvailable = scope.length > 0
    const ttlValid = isValidBulkTtl(defaultTtl)
    const ptrVisible = step === 'preview' && !!preview && !preview.blocking && touchesPtrTypes(preview)
    const applyAllowed = step === 'preview' && canApply(preview, { confirmRemoval, busy, applyIssues })
    const changeCount = previewChanges(preview).length

    const requestClose = useCallback(() => {
        if (busy) return
        if (dirty && !window.confirm(t('bulk.discardConfirm'))) return
        onClose()
    }, [busy, dirty, onClose, t])

    // ESC schliesst (nicht waehrend eines Requests), Strg/Cmd+Enter = Vorschau bzw. Anwenden
    useEffect(() => {
        function onKey(e) {
            if (e.key === 'Escape') {
                e.preventDefault()
                requestClose()
            } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                if (primaryRef.current && !primaryRef.current.disabled) primaryRef.current.click()
            }
        }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [requestClose])

    async function sendPreview(request) {
        setBusy(true)
        setError('')
        setConflict(false)
        setApplyIssues([])
        try {
            const res = await api.previewBulkRecords(server, zoneId, request)
            setLastRequest(request)
            const lines = request?.text ? lineIssuesOf(res) : []
            if (lines.length > 0) {
                setLineIssues(lines)
                setPreview(null)
                setStep('text')
            } else {
                setLineIssues([])
                setPreview(res)
                setConfirmRemoval(false)
                setStep('preview')
            }
        } catch (err) {
            if (err?.name === 'AbortError') return
            setError(err?.message || String(err))
        } finally {
            setBusy(false)
        }
    }

    function runTextPreview() {
        if (busy) return
        if (isTextEmpty(text)) {
            setLineIssues([])
            setError(t('bulk.emptyText'))
            return
        }
        if (!ttlValid) {
            setError(t('bulk.ttlRange'))
            return
        }
        sendPreview(buildTextRequest({ text, mode, scope, defaultTtl }))
    }

    function refreshPreview() {
        const request = textFlow && lastRequest?.text ? lastRequest : refreshRequest(lastRequest)
        if (request) sendPreview(request)
    }

    async function apply() {
        if (!applyAllowed) return
        const body = { ...preview.ops }
        if (ptrVisible) body.manage_ptr = ptrChoice
        setBusy(true)
        setError('')
        setApplyIssues([])
        try {
            const res = await api.bulkRecords(server, zoneId, body)
            setBusy(false)
            onApplied(res, { managePtr: ptrVisible ? ptrChoice : null })
        } catch (err) {
            setBusy(false)
            if (isConflictError(err)) {
                setConflict(true)
                setError(t('bulk.conflict'))
                return
            }
            const issues = applyErrorIssues(err)
            if (issues.length > 0) setApplyIssues(issues)
            setError(err?.message || String(err))
        }
    }

    function loadAll() {
        if (text.trim() && !window.confirm(t('bulk.loadAllConfirm'))) return
        const p = loadAllPrefill(records, zoneKey)
        setText(p.text)
        setMode('sync_scope')
        setScope(p.scope)
        setLoadedHint({ rrsets: p.rrsets, values: p.values })
        setLineIssues([])
        setError('')
    }

    function jumpToLine(line) {
        const ta = textareaRef.current
        if (!ta) return
        const [start, end] = lineOffsets(text, line)
        ta.focus()
        ta.setSelectionRange(start, end)
        const lineHeight = parseFloat(window.getComputedStyle(ta).lineHeight) || 16
        ta.scrollTop = Math.max(0, (Number(line) - 3) * lineHeight)
    }

    function changePtr(v) {
        setPtrChoice(!!v)
        setManagePtr(zoneKey, !!v)
    }

    const ptrChecked = ptrChoice === null ? getDefault() : ptrChoice

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={requestClose}
        >
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="glass-card p-6 w-full max-w-4xl max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between mb-4 gap-3">
                    <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2">
                        <FileText className="w-5 h-5 text-accent-light" aria-hidden="true" />
                        {step === 'text' ? t('bulk.editorTitle') : t('bulk.previewTitle')}
                    </h2>
                    <button
                        type="button"
                        onClick={requestClose}
                        disabled={busy}
                        className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-50"
                        aria-label={t('common.close')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <ModalErrorBanner message={error} onClose={busy ? undefined : () => setError('')} />

                {conflict && (
                    <div className="mb-4 flex justify-end">
                        <button
                            type="button"
                            onClick={refreshPreview}
                            disabled={busy}
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-border text-text-primary hover:bg-bg-hover disabled:opacity-50"
                        >
                            <RefreshCw className="w-4 h-4" aria-hidden="true" /> {t('bulk.refreshPreview')}
                        </button>
                    </div>
                )}

                {step === 'text' && (
                    <div className="space-y-4">
                        <fieldset>
                            <legend className="text-sm font-medium text-text-secondary mb-2">{t('bulk.modeLabel')}</legend>
                            <div className="grid gap-2 sm:grid-cols-3">
                                {TEXT_MODES.map((m) => {
                                    const unavailable = m === 'sync_scope' && !syncAvailable
                                    return (
                                        <label
                                            key={m}
                                            className={`p-3 rounded-lg border text-sm cursor-pointer ${mode === m ? 'border-accent bg-accent/10' : 'border-border'} ${unavailable ? 'opacity-50 cursor-not-allowed' : ''}`}
                                        >
                                            <span className="flex items-center gap-2 font-medium text-text-primary">
                                                <input
                                                    type="radio"
                                                    name={`${titleId}-mode`}
                                                    value={m}
                                                    checked={mode === m}
                                                    disabled={unavailable || busy}
                                                    onChange={() => setMode(m)}
                                                />
                                                {t(MODE_LABELS[m])}
                                            </span>
                                            <span className="block mt-1 text-xs text-text-muted">
                                                {unavailable ? t('bulk.modeSyncUnavailable') : t(MODE_HELP[m])}
                                            </span>
                                        </label>
                                    )
                                })}
                            </div>
                        </fieldset>

                        {loadedHint && (
                            <p className="text-xs text-text-muted" role="status">
                                {t('bulk.loadedHint', { rrsets: loadedHint.rrsets, values: loadedHint.values })}
                            </p>
                        )}

                        <div>
                            <div className="flex items-center justify-between mb-1 gap-2">
                                <label htmlFor={`${titleId}-text`} className="text-sm font-medium text-text-secondary">
                                    {t('bulk.editorTitle')}
                                </label>
                                <button
                                    type="button"
                                    onClick={loadAll}
                                    disabled={busy}
                                    className="text-xs text-accent-light hover:underline disabled:opacity-50"
                                >
                                    {t('bulk.loadAll')}
                                </button>
                            </div>
                            <textarea
                                id={`${titleId}-text`}
                                ref={textareaRef}
                                value={text}
                                onChange={(e) => setText(e.target.value)}
                                rows={18}
                                spellCheck={false}
                                wrap="off"
                                disabled={busy}
                                className="w-full font-mono text-xs p-3 rounded-lg border border-border bg-bg-primary text-text-primary overflow-auto"
                            />
                        </div>

                        {lineIssues.length > 0 && (
                            <div className="p-3 rounded-xl border bg-danger/10 border-danger/30 text-danger" role="alert">
                                <p className="text-sm font-medium">{t('bulk.parseErrorsTitle')}</p>
                                <ul className="mt-2 space-y-1">
                                    {lineIssues.map((issue, i) => {
                                        const { key, values } = issueI18n(issue)
                                        return (
                                            <li key={`${issue.line}-${issue.code}-${i}`}>
                                                <button
                                                    type="button"
                                                    onClick={() => jumpToLine(issue.line)}
                                                    className={`text-left text-xs hover:underline ${issue.severity === 'error' ? '' : 'text-warning'}`}
                                                >
                                                    {needsLinePrefix(issue) && (
                                                        <span className="font-medium">{t('bulk.lineLabel', { line: issue.line })}: </span>
                                                    )}
                                                    {t(key, { ...values, defaultValue: issue.message })}
                                                </button>
                                            </li>
                                        )
                                    })}
                                </ul>
                            </div>
                        )}

                        <div className="grid gap-4 sm:grid-cols-2">
                            <div>
                                <label htmlFor={`${titleId}-ttl`} className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('bulk.defaultTtl')}
                                </label>
                                <TtlInput id={`${titleId}-ttl`} value={defaultTtl} onChange={setDefaultTtl} disabled={busy} min={TTL_MIN} max={TTL_MAX} />
                            </div>
                            <details className="text-xs text-text-secondary">
                                <summary className="cursor-pointer text-sm font-medium text-text-secondary">{t('bulk.syntaxHelpTitle')}</summary>
                                <p className="mt-2">{t('bulk.syntaxHelpBody')}</p>
                                <p className="mt-2 font-medium">{t('bulk.syntaxExample')}</p>
                                <pre className="mt-1 p-2 rounded bg-bg-primary border border-border overflow-x-auto">{SYNTAX_EXAMPLE}</pre>
                            </details>
                        </div>
                    </div>
                )}

                {step === 'preview' && (
                    <div className="space-y-4">
                        {busy && !preview && (
                            <div className="flex items-center gap-3 py-8 justify-center text-text-muted text-sm" role="status">
                                <Loader2 className="w-5 h-5 animate-spin text-accent" aria-hidden="true" /> {t('bulk.previewLoading')}
                            </div>
                        )}
                        <BulkPreviewView
                            preview={preview}
                            zoneKey={zoneKey}
                            confirmRemoval={confirmRemoval}
                            onConfirmRemovalChange={setConfirmRemoval}
                            applyIssues={applyIssues}
                        />
                        {ptrVisible && (
                            <PtrSyncOption checked={ptrChecked} onChange={changePtr} values={[]} server={server} lookup={false} />
                        )}
                    </div>
                )}

                <div className="flex flex-wrap justify-end gap-2 mt-6">
                    <button
                        type="button"
                        onClick={requestClose}
                        disabled={busy}
                        className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                    >
                        {t('common.cancel')}
                    </button>
                    {step === 'preview' && textFlow && (
                        <button
                            type="button"
                            onClick={() => { setStep('text'); setError(''); setConflict(false); setApplyIssues([]) }}
                            disabled={busy}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                        >
                            <ArrowLeft className="w-4 h-4" aria-hidden="true" /> {t('bulk.backToEditor')}
                        </button>
                    )}
                    {step === 'text' ? (
                        <button
                            ref={primaryRef}
                            type="button"
                            onClick={runTextPreview}
                            disabled={busy}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                            {busy ? t('bulk.previewLoading') : t('bulk.preview')}
                        </button>
                    ) : (
                        <button
                            ref={primaryRef}
                            type="button"
                            onClick={apply}
                            disabled={!applyAllowed}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('bulk.apply', { count: changeCount })}
                        </button>
                    )}
                </div>
            </div>
        </div>
    )
}
