import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import api from '../api'
import { isValidIP } from '../lib/ip.js'
import { getPtrConfig, loadPtrConfig, subscribePtrConfig } from '../lib/ptrPreference.js'
import { lookupCandidates, lookupLine } from '../lib/ptrResults.js'

// Checkbox "PTR in Reverse-Zone mitpflegen" (F11 §2.5 Schritt 2, §2.6). Genutzt vom Record-Dialog
// (zoneDetail/form-extensions/ptr.ext.jsx) und vom Bulk-Editor (F1, ohne Live-Pruefung: lookup={false}).
//
// Props:
//   checked, onChange(bool)   Zustand der Checkbox (das Merken uebernimmt der Aufrufer, lib/ptrPreference.js)
//   values: string[]          eingegebene Werte (A/AAAA); nur gueltige IPs werden geprueft, hoechstens 3
//   target                    FQDN, auf den der PTR zeigen soll (Ziel der Live-Pruefung)
//   server                    Servername aus der URL (Reihenfolge der Reverse-Zonen-Suche im Backend)
//   isEdit                    Bearbeiten: Hinweis, dass der PTR der bisherigen IP entfernt wird
//   reverseZonesAvailable     Anzahl beschreibbarer Reverse-Zonen; undefined -> aus GET /ptr/config (Cache)
//   lookup = true             Live-Pruefung (GET /ptr/lookup, 400 ms entprellt, veraltete Antworten verworfen)
//   disabled                  Checkbox gesperrt (kein Schreibrecht)
const LOOKUP_DELAY_MS = 400
const MAX_LOOKUPS = 3

const TONE_CLASS = {
    ok: 'text-success',
    warn: 'text-amber-300',
    muted: 'text-text-muted',
}

function usePtrConfig() {
    const cfg = useSyncExternalStore(subscribePtrConfig, getPtrConfig, getPtrConfig)
    useEffect(() => {
        // Cache (60 s) - mehrere Dialoge loesen hoechstens einen Request aus; Fehler bleiben still (Spec §2.8)
        loadPtrConfig(() => api.getPtrConfig())
    }, [])
    return cfg
}

export default function PtrSyncOption({
    checked, onChange, values = [], target = '', server = '', isEdit = false,
    reverseZonesAvailable, lookup = true, disabled = false,
}) {
    const { t } = useTranslation()
    const inputId = useId()
    const hintId = useId()
    const cfg = usePtrConfig()
    const zonesAvailable = reverseZonesAvailable === undefined ? cfg.reverse_zones_available : reverseZonesAvailable

    // Live-Pruefung: Schluessel aus den aktuellen Eingaben; Ergebnisse gelten nur, solange der Schluessel passt
    // (so muss der Effekt beim Leeren der Eingaben keinen Zustand synchron zuruecksetzen).
    const candidates = lookup && checked && target ? lookupCandidates(values, isValidIP, MAX_LOOKUPS) : []
    const lookupKey = candidates.length ? JSON.stringify([candidates, target, server || '']) : ''
    const [result, setResult] = useState({ key: '', items: [] })
    const seqRef = useRef(0)

    useEffect(() => {
        if (!lookupKey) return undefined
        const [ips, name, srv] = JSON.parse(lookupKey)
        const seq = ++seqRef.current
        const ctrl = new AbortController()
        const timer = setTimeout(async () => {
            const items = await Promise.all(ips.map((ip) => api.lookupPtr(ip, name, srv, { signal: ctrl.signal })
                .then((res) => ({ ip, line: lookupLine(res, { ip, target: name }) }))
                .catch((err) => (err?.name === 'AbortError'
                    ? null
                    : { ip, line: { tone: 'muted', key: 'ptr.lookupError', values: { ip } } }))))
            // Veraltete Antwort (Eingabe hat sich inzwischen geaendert) verwerfen
            if (seq !== seqRef.current || ctrl.signal.aborted) return
            setResult({ key: lookupKey, items: items.filter(Boolean) })
        }, LOOKUP_DELAY_MS)
        return () => {
            clearTimeout(timer)
            ctrl.abort()
        }
    }, [lookupKey])

    const checking = !!lookupKey && result.key !== lookupKey
    const items = lookupKey && result.key === lookupKey ? result.items : []

    return (
        <div className="rounded-lg border border-border/70 bg-bg-secondary/40 p-3 space-y-1.5">
            <label htmlFor={inputId} className="flex items-start gap-2 cursor-pointer text-sm text-text-secondary">
                <input
                    id={inputId}
                    type="checkbox"
                    checked={!!checked}
                    disabled={disabled}
                    onChange={(e) => onChange?.(e.target.checked)}
                    aria-describedby={hintId}
                    className="w-4 h-4 rounded mt-0.5"
                />
                <span>{t('ptr.manage')}</span>
            </label>
            <p id={hintId} className="text-xs text-text-muted leading-snug pl-6">{t('ptr.manageHint')}</p>
            {zonesAvailable === 0 && (
                <p className="text-xs text-text-muted leading-snug pl-6">{t('ptr.noReverseZones')}</p>
            )}
            {checked && isEdit && (
                <p className="text-xs text-text-muted leading-snug pl-6">{t('ptr.updateNote')}</p>
            )}
            {lookup && checked && (checking || items.length > 0) && (
                <ul className="pl-6 space-y-0.5 text-xs" aria-live="polite">
                    {checking && (
                        <li className="flex items-center gap-1.5 text-text-muted">
                            <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
                            {t('ptr.lookupChecking')}
                        </li>
                    )}
                    {!checking && items.map(({ ip, line }) => (
                        <li key={ip} className={`break-words ${TONE_CLASS[line.tone] || TONE_CLASS.muted}`}>
                            {t(line.key, line.values)}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}
