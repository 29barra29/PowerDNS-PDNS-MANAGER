import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api from '../api'
import { normalizeRecordName } from '../lib/dnsName.js'
import { fanoutSummary, formatFanoutErrors, formatFanoutWarnings } from '../lib/fanout.js'
import { luaInactiveServers as inactiveLuaServers, relevantLuaServers } from '../lib/luaRecord.js'
import { effectiveManagePtr, isPtrType, loadPtrConfig } from '../lib/ptrPreference.js'
import { applyPtrMessages } from '../lib/ptrResults.js'
import { rrsetValueCount, truncateValue } from '../lib/recordContent.js'

// Daten und Handler der Zonenansicht (Plan B.14). Verhalten wie 2.4.1 (ZoneDetailPage), mit diesen Aenderungen:
//  - loadZone laedt Records und Zonen-Metadaten parallel (F8-F08); DNSSEC-Daten laedt der DNSSEC-Abschnitt selbst
//    ueber subscribeZoneLoaded (F4-B ersetzt ihn, ohne Shell oder Hook anzufassen).
//  - handleDelete(record, extra?) fragt mit dem konkreten Wert nach (letzter Wert des RRsets = ganzer Eintrag) und
//    wertet das Fan-out-Ergebnis aus (lib/fanout.js), wie Anlegen/Aendern (F8-F09).
//  - Peer-Fehler erscheinen rot als zoneDetail.fanoutPartialError, nicht geladene Server
//    ('skipped (not loaded: ...)') als gelbe Warnung [D4].
//  - resolveName normalisiert wie der Record-Dialog (lib/dnsName.js, F8-F05).
//  - PTR-Pflege (F11 §2.5 Schritt 5, WS-F9F11-FE): handleDelete sendet bei A/AAAA immer manage_ptr (Auswahl aus
//    lib/ptrPreference.js, sonst Admin-Default; ein manage_ptr in `extra` gewinnt), ergaenzt die Rueckfrage um
//    ptr.deleteNote und wertet details.ptr aus (lib/ptrResults.js). GET /ptr/config laedt der Hook beim Oeffnen.
//  - LUA (F15 6.6, WS-F15): luaPolicy (GET /lua/policy beim Oeffnen; sicherer Startwert = kein Schreibrecht, das
//    Backend entscheidet ohnehin), luaStatus/luaStatusLoading/luaStatusError (GET /lua/server-status, hoechstens
//    einmal je Seitenaufruf ueber ensureLuaStatus(): nach dem Laden, wenn die Zone LUA-Records hat, und wenn der
//    Record-Dialog LUA zeigt), luaRelevantServers (aktueller Server + schreibbare, erreichbare Peers) und
//    luaInactiveServers (davon mit enable-lua-records=no).
// TTL (F01) und disabled (F07) setzt der Record-Dialog (RecordFormModal) aus dem Record der Anfrage.
// Die Schnittstelle ist in zoneDetailContext.js beschrieben (LUA-Felder siehe oben).
const LUA_POLICY_FALLBACK = Object.freeze({ policy: 'admin', can_write: false, reason: null })

export default function useZoneData(server, zoneId) {
    const { t } = useTranslation()

    const [records, setRecords] = useState([])
    /** Metadaten von GET /zones/.../detail (u. a. dnssec: boolean, kind, serial) */
    const [zoneMeta, setZoneMeta] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [warning, setWarning] = useState('')
    /** Offener Record-Dialog: { seq, mode: 'add'|'edit'|'clone', record } oder null */
    const [recordForm, setRecordForm] = useState(null)
    const [slotState, setSlotStateMap] = useState({})
    /** letztes Lade-Ereignis; ein Effekt verteilt es an die Abonnenten */
    const [loadEvent, setLoadEvent] = useState(null)
    /** seq des letzten erfolgreichen Ladevorgangs (0 = noch keiner) */
    const [zoneLoadSeq, setZoneLoadSeq] = useState(0)

    const [me, setMe] = useState(() => api.getUser())
    const [allServers, setAllServers] = useState([])

    // LUA (F15): Policy fuer diesen Nutzer und PowerDNS-Status der Server
    const [luaPolicy, setLuaPolicy] = useState(LUA_POLICY_FALLBACK)
    const [luaStatus, setLuaStatus] = useState(null)
    const [luaStatusLoading, setLuaStatusLoading] = useState(false)
    const [luaStatusError, setLuaStatusError] = useState('')

    const loadSeqRef = useRef(0)
    const formSeqRef = useRef(0)
    const listenersRef = useRef(new Set())
    const luaStatusRequested = useRef(false)

    useEffect(() => {
        api.getMe().then((u) => { api.setUser(u); setMe(u) }).catch(() => {})
    }, [])
    useEffect(() => {
        api.getServers().then((d) => setAllServers(d.servers || [])).catch(() => {})
    }, [])
    // Admin-Default der PTR-Pflege (Cache in lib/ptrPreference.js; Fehler bleiben still, Default "aus")
    useEffect(() => {
        loadPtrConfig(() => api.getPtrConfig())
    }, [])
    // LUA-Policy: Fehler bleiben still, der sichere Startwert (kein Schreibrecht) bleibt stehen
    useEffect(() => {
        const ctrl = new AbortController()
        api.getLuaPolicy({ signal: ctrl.signal })
            .then((p) => { if (p && typeof p === 'object') setLuaPolicy(p) })
            .catch(() => {})
        return () => ctrl.abort()
    }, [])

    /** LUA-Server-Status einmal je Seitenaufruf laden (Aufruf aus Handlern/Effekten; setzt Zustand asynchron). */
    const ensureLuaStatus = useCallback(() => {
        if (luaStatusRequested.current) return
        luaStatusRequested.current = true
        queueMicrotask(async () => {
            setLuaStatusLoading(true)
            try {
                const st = await api.getLuaServerStatus()
                setLuaStatus(st && typeof st === 'object' ? st : null)
                setLuaStatusError('')
            } catch (err) {
                setLuaStatusError(err?.message || String(err))
            } finally {
                setLuaStatusLoading(false)
            }
        })
    }, [])

    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    const zoneName = (zoneId || '').replace(/\.$/, '')
    const zoneKey = useMemo(() => {
        const z = (zoneId || '').trim().toLowerCase()
        if (!z) return ''
        return z.endsWith('.') ? z : `${z}.`
    }, [zoneId])

    const currentServerInfo = useMemo(
        () => allServers.find((s) => s.name === server) || null,
        [allServers, server]
    )
    const otherWritableServers = useMemo(
        () => allServers.filter((s) => s.name !== server && s.allow_writes !== false && s.is_reachable),
        [allServers, server]
    )
    const luaRelevantServers = useMemo(() => relevantLuaServers(server, allServers), [server, allServers])
    const luaInactiveServers = useMemo(
        () => inactiveLuaServers(luaStatus, luaRelevantServers),
        [luaStatus, luaRelevantServers]
    )
    const serverCanWrite = currentServerInfo ? currentServerInfo.allow_writes !== false : true
    const userCanEdit = !me || me.role === 'admin' || me.zone_permissions?.[zoneKey] !== 'read'
    const canEdit = userCanEdit && serverCanWrite
    const isAdmin = me?.role === 'admin'

    /** Laedt Records + Metadaten parallel; setzt Zustand erst nach dem Laden (keine synchronen setState-Aufrufe). */
    const fetchZone = useCallback(async (silent) => {
        const seq = ++loadSeqRef.current
        const [recRes, metaRes] = await Promise.allSettled([
            api.listRecords(server, zoneId),
            api.getZone(server, zoneId),
        ])
        // Ein neuerer Ladevorgang hat uebernommen - dessen Ergebnis gilt.
        if (seq !== loadSeqRef.current) return { seq, ok: false, stale: true, silent }
        let event
        if (recRes.status === 'fulfilled') {
            const list = recRes.value?.records || []
            const meta = metaRes.status === 'fulfilled' ? metaRes.value : null
            setRecords(list)
            setZoneMeta(meta)
            setZoneLoadSeq(seq)
            if (list.some((r) => r?.type === 'LUA')) ensureLuaStatus()
            event = { seq, ok: true, silent, records: list, zoneMeta: meta, error: null }
        } else {
            const err = recRes.reason
            setError(err?.message || String(err))
            event = { seq, ok: false, silent, records: null, zoneMeta: null, error: err }
        }
        setLoading(false)
        setLoadEvent(event)
        return event
    }, [server, zoneId, ensureLuaStatus])

    /** Zone neu laden. opts.silent: ohne Ganzseiten-Spinner (Panels bleiben gemountet, F7 §6.4). */
    const loadZone = useCallback((opts = {}) => {
        const silent = !!opts.silent
        if (!silent) setLoading(true)
        return fetchZone(silent)
    }, [fetchZone])

    // Erstes Laden (loading ist bereits true); per Microtask, damit der Effekt selbst keinen Zustand setzt.
    useEffect(() => { queueMicrotask(() => { fetchZone(false) }) }, [fetchZone])

    /** Abonnenten werden nach dem Rendern des neuen Stands benachrichtigt (Kind-Effekte laufen vorher). */
    useEffect(() => {
        if (!loadEvent) return
        const report = (err) => console.error('subscribeZoneLoaded: Abonnent hat geworfen', err)
        for (const cb of Array.from(listenersRef.current)) {
            try {
                const ret = cb(loadEvent)
                // asynchrone Abonnenten: Ablehnung nicht unbehandelt lassen
                if (ret && typeof ret.catch === 'function') ret.catch(report)
            } catch (err) {
                report(err)
            }
        }
    }, [loadEvent])

    const subscribeZoneLoaded = useCallback((cb) => {
        if (typeof cb !== 'function') return () => {}
        listenersRef.current.add(cb)
        return () => { listenersRef.current.delete(cb) }
    }, [])

    const setSlotState = useCallback((key, value) => {
        setSlotStateMap((prev) => {
            const next = typeof value === 'function' ? value(prev[key]) : value
            if (Object.is(prev[key], next)) return prev
            return { ...prev, [key]: next }
        })
    }, [])

    /** Fan-out-Ergebnis (details bzw. details.fanout) auswerten: Fehler rot, nicht geladene Server gelb. */
    const reportFanout = useCallback((details) => {
        const summary = fanoutSummary(details)
        // Wie 2.4.1: Teilfehler auf Peer-Servern nicht verschlucken (Primary war ok, Peer nicht).
        if (summary.hasErrors) setError(t('zoneDetail.fanoutPartialError', { errors: formatFanoutErrors(summary.errors) }))
        setWarning(summary.hasWarnings
            ? t('zoneDetail.fanoutNotLoaded', { servers: formatFanoutWarnings(summary.warnings) })
            : '')
        return summary
    }, [t])

    /** Relativer oder absoluter Name -> FQDN mit Punkt (klein); ungueltige Eingaben -> '' (Fehlertext: Dialog). */
    const resolveName = useCallback((name) => normalizeRecordName(name, zoneName).fqdn || '', [zoneName])

    const relativeName = useCallback((fqdn) => {
        let name = String(fqdn || '').replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        return name
    }, [zoneName])

    const openAdd = useCallback(() => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'add', record: null })
    }, [])
    /** Bestehenden Record klonen -> Add-Dialog mit denselben Werten, aber ohne oldContent. */
    const openClone = useCallback((record) => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'clone', record })
    }, [])
    const openEdit = useCallback((record) => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'edit', record })
    }, [])
    const closeRecordForm = useCallback(() => setRecordForm(null), [])

    /**
     * Einen Wert loeschen (Papierkorb). extra: zusaetzliche Body-Felder (z. B. { manage_ptr }); name/type/content
     * gewinnen immer. Bei A/AAAA wird manage_ptr immer gesendet (extra.manage_ptr, sonst gemerkte Auswahl bzw.
     * Admin-Default) und details.ptr ausgewertet. Liefert die Antwort oder null (abgebrochen/Fehler).
     */
    const handleDelete = useCallback(async (record, extra = null) => {
        const { name, type, content } = record
        const body = { ...(extra || {}) }
        if (isPtrType(type)) {
            if (typeof body.manage_ptr !== 'boolean') body.manage_ptr = effectiveManagePtr(zoneKey)
        } else {
            delete body.manage_ptr
        }
        // Letzter Wert des RRsets -> der ganze Eintrag verschwindet; die Rueckfrage sagt das ausdruecklich (F8-F09)
        const isLast = rrsetValueCount(records, name, type) <= 1
        let question = t(isLast ? 'zoneDetail.deleteLastValueConfirm' : 'zoneDetail.deleteValueConfirm', {
            value: truncateValue(content, 80),
            name: String(name || '').replace(/\.$/, ''),
            type,
        })
        if (body.manage_ptr === true) question = `${question}\n\n${t('ptr.deleteNote')}`
        if (!window.confirm(question)) return null
        try {
            const res = await api.deleteRecord(server, zoneId, { ...body, name, type, content })
            reportFanout(res?.details)
            setSuccess(t('zoneDetail.recordDeleted', { type, name: name.replace(/\.$/, '') }))
            // PTR-Ergebnis: entfernte PTRs an die Erfolgsmeldung, Probleme ins gelbe Banner (F11 §2.5 Schritt 4)
            applyPtrMessages(t, res?.details, { setSuccess, setWarning })
            loadZone()
            return res
        } catch (err) {
            setError(err.message)
            return null
        }
    }, [t, server, zoneId, zoneKey, records, reportFanout, loadZone])

    return {
        server, zoneId, zoneName, zoneKey,
        records, zoneMeta, loading, zoneLoadSeq,
        me, isAdmin, allServers, currentServerInfo, otherWritableServers,
        userCanEdit, serverCanWrite, canEdit,
        error, setError, success, setSuccess, warning, setWarning,
        loadZone, subscribeZoneLoaded,
        recordForm, openAdd, openEdit, openClone, closeRecordForm,
        handleDelete, reportFanout, resolveName, relativeName,
        slotState, setSlotState,
        luaPolicy, luaStatus, luaStatusLoading, luaStatusError, ensureLuaStatus, luaRelevantServers, luaInactiveServers,
    }
}
