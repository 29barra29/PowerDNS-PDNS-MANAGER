// Reine Hilfsfunktionen fuer den Propagations-Check (F12 §6.4) und den Monitoring-Tab (F12 §2.2, F13 §2.3).
// Ohne React-/i18n-Import, damit `node --test` die Datei laden kann (frontend/tests/propagationModel.test.mjs).
// Funktionen, die Texte liefern, bekommen `t` (i18next) als Parameter.
//
// API-Vertrag: GET /zones/{server}/{zone}/propagation (F12 §3.1) plus Ergaenzungen aus WS-F12F13-BE:
// `separate_backends` (oberste Ebene), Fehlercode `not_loaded`; Peer-Zeilen mit Notiz `separate_backend`
// entscheiden ueber den Inhalt (Fingerprint), die Serial ist dort nur Info [D9].

export const KIND_ORDER = ['panel-api', 'authoritative', 'resolver']

export const KIND_LABEL_KEYS = {
    'panel-api': 'propagation.groupPanel',
    authoritative: 'propagation.groupAuthoritative',
    resolver: 'propagation.groupResolver',
}

const STATUS_LABEL_KEYS = {
    ok: 'propagation.statusOk',
    mismatch: 'propagation.statusMismatch',
    error: 'propagation.statusError',
    timeout: 'propagation.statusTimeout',
    skipped: 'propagation.statusSkipped',
}

const BADGE_BASE = 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap'
const STATUS_BADGE_CLASSES = {
    ok: 'bg-success/10 text-success border-success/30',
    mismatch: 'bg-warning/10 text-warning border-warning/30',
    error: 'bg-danger/10 text-danger border-danger/30',
    timeout: 'bg-danger/10 text-danger border-danger/30',
    skipped: 'bg-bg-secondary text-text-muted border-border',
}

// Quellen nach Art gruppieren: [{ kind, labelKey, rows }] - nur nicht-leere Gruppen, Reihenfolge KIND_ORDER.
// Die Reihenfolge innerhalb einer Gruppe kommt vom Backend (Referenz zuerst, dann Name/Konfiguration).
// Unbekannte Arten landen hinten in eigenen Gruppen (Vorwaertskompatibilitaet).
export function groupSources(sources) {
    const list = Array.isArray(sources) ? sources : []
    const byKind = new Map()
    for (const row of list) {
        if (!row || typeof row !== 'object') continue
        const kind = row.kind || 'unknown'
        if (!byKind.has(kind)) byKind.set(kind, [])
        byKind.get(kind).push(row)
    }
    const kinds = [...KIND_ORDER.filter((k) => byKind.has(k)), ...[...byKind.keys()].filter((k) => !KIND_ORDER.includes(k))]
    return kinds.map((kind) => ({ kind, labelKey: KIND_LABEL_KEYS[kind] || null, rows: byKind.get(kind) }))
}

// Tailwind-Klassen des Status-Badges (ok gruen, mismatch gelb, error/timeout rot, skipped grau)
export function statusBadgeClass(status) {
    return `${BADGE_BASE} ${STATUS_BADGE_CLASSES[status] || STATUS_BADGE_CLASSES.skipped}`
}

export function statusLabelKey(status) {
    return STATUS_LABEL_KEYS[status] || STATUS_LABEL_KEYS.skipped
}

// Uebersetzter Notiz-Text (Codes F12 §3.1.2). Unbekannte Codes erscheinen roh.
export function noteText(t, code, row) {
    return t(`propagation.note_${code}`, { ttl: row?.ttl ?? '?', defaultValue: code })
}

// Uebersetzter Fehlertext zu row.error_code; Fallback ist der deutsche Backend-Text (row.error).
// Fuer Admins haengt das Backend bei api_error die PowerDNS-Meldung an - die bleibt dann erhalten.
export function errorText(t, row) {
    if (!row || !row.error_code) return ''
    const base = t(`propagation.err_${row.error_code}`, { defaultValue: row.error || row.error_code })
    if (row.error_code === 'api_error' && typeof row.error === 'string') {
        const idx = row.error.indexOf(': ')
        if (idx > 0) return `${base}: ${row.error.slice(idx + 2)}`
    }
    return base
}

// Peer mit getrennter Datenbank, dessen Inhalt gleich ist (Status ok trotz abweichender Serial) [D9]
export function isSeparateBackendSame(row) {
    return !!row && row.kind === 'panel-api' && !row.is_reference && row.status === 'ok'
        && Array.isArray(row.notes) && row.notes.includes('separate_backend') && row.content_match === true
}

// Serial mit Relation, z. B. "2026100503 (aelter)". Bei getrennten Backends mit gleichem Inhalt ist die
// abweichende Serial normal - dort ohne "aelter/neuer" (Hinweisspalte erklaert es).
export function formatSerial(row, t) {
    if (!row || row.serial === null || row.serial === undefined) return '–'
    const serial = String(row.serial)
    if (isSeparateBackendSame(row)) return serial
    if (row.serial_relation === 'behind') return `${serial} (${t('propagation.serialBehind')})`
    if (row.serial_relation === 'ahead') return `${serial} (${t('propagation.serialAhead')})`
    return serial
}

// Ton der Serial-Zelle: 'muted' (keine Serial), 'warn' (weicht ab und zaehlt), 'normal'
export function serialTone(row) {
    if (!row || row.serial === null || row.serial === undefined) return 'muted'
    if (row.match === false && !isSeparateBackendSame(row)) return 'warn'
    return 'normal'
}

// Inhalt der Record-Spalte (nur bei Record-Vergleich):
//   { kind: 'none' }                       kein Wert (Abfrage fehlgeschlagen, Quelle uebersprungen ...)
//   { kind: 'empty', match }               leere Antwort (NXDOMAIN/NODATA) -> "(kein Eintrag)"
//   { kind: 'values', values, match }      Werte (monospace); match = true/false/null (nicht vergleichbar)
export function recordCell(row) {
    if (!row || !Array.isArray(row.record_values)) return { kind: 'none' }
    const match = row.record_match === true || row.record_match === false ? row.record_match : null
    if (row.record_values.length === 0) return { kind: 'empty', match }
    return { kind: 'values', values: row.record_values.map(String), match }
}

// Inhaltsvergleich einer Panel-Zeile: null oder { key, params, ok, sample }
// - content_match true  -> "Inhalt gleich" bzw. bei getrennten Backends "Inhalt gleich, Serial abweichend
//   (getrennte Datenbank)" [D9]
// - content_match false -> "Inhalt abweichend (n RRsets)" + bis zu 5 Beispiele fuer den Tooltip
export function contentInfo(row) {
    if (!row || row.kind !== 'panel-api' || row.is_reference) return null
    if (row.content_match !== true && row.content_match !== false) return null
    const sample = Array.isArray(row.content_diff_sample) ? row.content_diff_sample.slice(0, 5).map(String) : []
    if (row.content_match) {
        if (isSeparateBackendSame(row) && row.match === false) {
            return { key: 'propagation.contentSameSeparate', params: {}, ok: true, sample }
        }
        return { key: 'propagation.contentSame', params: {}, ok: true, sample }
    }
    return { key: 'propagation.contentDiffers', params: { count: row.content_diff_count ?? sample.length }, ok: false, sample }
}

// Notiz-Codes, die in der Hinweisspalte erscheinen. Doppelte Aussagen entfallen: die Inhalts-Zeile
// "Inhalt gleich, Serial abweichend (getrennte Datenbank)" ersetzt `separate_backend` und
// `content_same_serial_differs`; bei abweichendem Inhalt ersetzt die Inhalts-Zeile `content_same_serial_differs`.
export function visibleNoteCodes(row) {
    const notes = Array.isArray(row?.notes) ? row.notes.filter((c) => typeof c === 'string' && c) : []
    const unique = [...new Set(notes)]
    const content = contentInfo(row)
    if (content && content.key === 'propagation.contentSameSeparate') {
        return unique.filter((c) => c !== 'separate_backend' && c !== 'content_same_serial_differs')
    }
    if (content && content.ok) return unique.filter((c) => c !== 'content_same_serial_differs')
    return unique
}

// Antwortzeit-Text oder ''
export function latencyText(t, row) {
    if (!row || row.latency_ms === null || row.latency_ms === undefined) return ''
    return t('propagation.latencyMs', { ms: row.latency_ms })
}

// Quelle mit Zusatz, z. B. "1.1.1.1 (Cloudflare)"
export function sourceLabel(row) {
    if (!row) return ''
    return row.label ? `${row.source} (${row.label})` : String(row.source ?? '')
}

// Zusammenfassung: { key, params, tone } - tone 'ok' (alle aktuell) oder 'warn'
// Uebersprungene Quellen zaehlt das Backend nicht in `total`.
export function summaryInfo(summary) {
    if (!summary || typeof summary !== 'object') return null
    const params = {
        total: summary.total ?? 0,
        ok: summary.ok ?? 0,
        mismatch: summary.mismatch ?? 0,
        failed: summary.failed ?? 0,
    }
    if (summary.in_sync) return { key: 'propagation.summaryInSync', params, tone: 'ok' }
    return { key: 'propagation.summaryPartial', params, tone: 'warn' }
}

// Hinweise ueber der Tabelle (F12 §2.1 Nr. 5/6): Liste von { id, key, tone } in fester Reihenfolge.
export function resultNotices(result) {
    if (!result || typeof result !== 'object') return []
    const out = []
    const external = result.external || {}
    const sources = Array.isArray(result.sources) ? result.sources : []
    const panelCount = sources.filter((s) => s && s.kind === 'panel-api').length
    if (result.timed_out) out.push({ id: 'timedOut', key: 'propagation.timedOut', tone: 'warn' })
    if (!external.enabled) out.push({ id: 'externalDisabled', key: 'propagation.externalDisabled', tone: 'info' })
    if (panelCount <= 1 && !external.enabled) out.push({ id: 'singleServer', key: 'propagation.singleServer', tone: 'info' })
    if (external.enabled && external.authoritative && (!Array.isArray(result.nameservers) || result.nameservers.length === 0)) {
        out.push({ id: 'noNameservers', key: 'propagation.noNameservers', tone: 'info' })
    }
    if (external.enabled && (!Array.isArray(external.resolvers) || external.resolvers.length === 0)) {
        out.push({ id: 'noResolvers', key: 'propagation.noResolvers', tone: 'info' })
    }
    if (result.separate_backends) out.push({ id: 'separateBackends', key: 'propagation.separateBackendsInfo', tone: 'info' })
    return out
}

// Abfrage-Parameter aus dem Formular; name ohne Typ ist ungueltig (Button gesperrt, Feldhinweis).
export function buildCheckParams({ recName, recType, compareContent } = {}) {
    const name = String(recName ?? '').trim()
    const type = String(recType ?? '').trim()
    return {
        name: name && type ? name : undefined,
        type: name && type ? type : undefined,
        content: !!compareContent,
    }
}

export function needsRecordType(recName, recType) {
    return String(recName ?? '').trim() !== '' && String(recType ?? '').trim() === ''
}

export function canRunCheck({ loading, recName, recType } = {}) {
    return !loading && !needsRecordType(recName, recType)
}

// =====================================================================================================
// Monitoring-Tab (Einstellungen): Resolver-Liste, Prometheus-Beispiele, Status-Karte
// =====================================================================================================

export const DEFAULT_RESOLVERS = ['1.1.1.1', '8.8.8.8', '9.9.9.9']

// Textarea -> Liste (eine Adresse je Zeile; leere Zeilen ignoriert). Validierung macht das Backend (422).
export function parseResolverText(text) {
    return String(text ?? '')
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean)
}

export function resolverText(list) {
    return (Array.isArray(list) ? list : []).join('\n')
}

export const SCRAPE_PLACEHOLDER_HOST = '<dein-host>'

// scheme/target/metrics_path aus scrape_url; ohne URL Platzhalter (F13 §2.3 Nr. 7).
// Port nur, wenn er in der URL steht.
export function scrapeTarget(scrapeUrl) {
    if (scrapeUrl) {
        try {
            const u = new URL(scrapeUrl)
            if (u.protocol === 'http:' || u.protocol === 'https:') {
                return {
                    scheme: u.protocol.replace(':', ''),
                    target: u.port ? `${u.hostname}:${u.port}` : u.hostname,
                    path: u.pathname && u.pathname !== '/' ? u.pathname : '/metrics',
                    url: `${u.protocol}//${u.host}${u.pathname && u.pathname !== '/' ? u.pathname : '/metrics'}`,
                    placeholder: false,
                }
            }
        } catch { /* ungueltige URL -> Platzhalter */ }
    }
    return {
        scheme: 'https',
        target: SCRAPE_PLACEHOLDER_HOST,
        path: '/metrics',
        url: `https://${SCRAPE_PLACEHOLDER_HOST}/metrics`,
        placeholder: true,
    }
}

export function buildScrapeYaml(scrapeUrl) {
    const { scheme, target, path } = scrapeTarget(scrapeUrl)
    return [
        'scrape_configs:',
        '  - job_name: pdns-manager',
        `    scheme: ${scheme}`,
        `    metrics_path: ${path}`,
        '    authorization:',
        '      type: Bearer',
        '      credentials_file: /etc/prometheus/pdns-manager.token',
        '    static_configs:',
        `      - targets: ['${target}']`,
    ].join('\n')
}

export function buildCurlCommand(scrapeUrl) {
    const { url } = scrapeTarget(scrapeUrl)
    return `curl -H "Authorization: Bearer $(cat pdns-manager.token)" ${url} | head`
}

// Nuetzliche Grafana/PromQL-Abfragen (nicht uebersetzt, F13 §2.3 Nr. 9)
export const GRAFANA_QUERIES = [
    'sum by (route) (rate(pdnsmgr_http_requests_total{status=~"5.."}[5m]))',
    'histogram_quantile(0.95, sum by (le, route) (rate(pdnsmgr_http_request_duration_seconds_bucket[5m])))',
    'min by (server) (pdnsmgr_pdns_server_up)',
    'sum by (method) (increase(pdnsmgr_login_attempts_total{result="failure"}[15m]))',
    'pdnsmgr_webhook_deliveries_pending',
].join('\n')

const SECRETS_MODE_KEYS = {
    encrypted: 'settings.monitoring.secretsModeEncrypted',
    plaintext_fallback: 'settings.monitoring.secretsModePlaintext',
    uninitialized: 'settings.monitoring.secretsModeUninitialized',
}

// Status-Karte aus GET /settings/monitoring/status: Zeilen { id, key, params, tone, detail? }
// tone: 'ok' | 'warn' | 'error' | 'info'; detail: Liste von { text } (roh) oder { key, params } (uebersetzt).
// Reine Ableitung, die Darstellung uebernimmt der Tab.
export function monitoringStatusRows(status) {
    if (!status || typeof status !== 'object') return []
    const rows = []
    const bg = status.background || {}
    const tasks = bg.tasks && typeof bg.tasks === 'object' ? bg.tasks : {}
    const taskNames = Object.keys(tasks).sort()
    const stopped = taskNames.filter((n) => !tasks[n]?.running)
    if (!bg.enabled) {
        rows.push({ id: 'background', key: 'settings.monitoring.statusBackgroundDisabled', params: {}, tone: 'info' })
    } else if (stopped.length > 0) {
        rows.push({
            id: 'background', key: 'settings.monitoring.statusBackgroundStopped',
            params: { list: stopped.join(', ') }, tone: 'error',
        })
    } else {
        rows.push({
            id: 'background', key: 'settings.monitoring.statusBackgroundOk',
            params: { n: taskNames.length }, tone: 'ok',
        })
    }

    const migrationErrors = Number(status.migration_errors) || 0
    rows.push(migrationErrors > 0
        ? { id: 'migrations', key: 'settings.monitoring.statusMigrationErrors', params: { n: migrationErrors }, tone: 'error' }
        : { id: 'migrations', key: 'settings.monitoring.statusMigrationsOk', params: {}, tone: 'ok' })

    const notLoaded = status.servers_not_loaded && typeof status.servers_not_loaded === 'object'
        ? Object.entries(status.servers_not_loaded) : []
    rows.push(notLoaded.length > 0
        ? {
            id: 'servers', key: 'settings.monitoring.statusServersNotLoaded',
            params: { list: notLoaded.map(([name]) => name).sort().join(', ') }, tone: 'error',
            detail: [...notLoaded].sort(([a], [b]) => a.localeCompare(b)).map(([name, reason]) => ({ text: `${name}: ${reason}` })),
        }
        : { id: 'servers', key: 'settings.monitoring.statusServersOk', params: {}, tone: 'ok' })

    const sec = status.secrets || {}
    const modeKey = SECRETS_MODE_KEYS[sec.mode] || null
    const unreadable = (Number(sec.unreadable_values) || 0) + (Number(sec.runtime_unreadable) || 0)
    const secretsTone = sec.mode !== 'encrypted' ? 'warn' : unreadable > 0 ? 'error' : 'ok'
    rows.push({
        id: 'secrets',
        key: modeKey || 'settings.monitoring.secretsModeUnknown',
        params: { mode: String(sec.mode ?? '?') },
        tone: secretsTone,
        detail: unreadable > 0 ? [{ key: 'settings.monitoring.statusSecretsUnreadable', params: { n: unreadable } }] : [],
    })
    return rows
}

// Liste der Hintergrund-Aufgaben fuer die Detailansicht: [{ name, running, last_run_at, last_error_at }]
export function backgroundTasks(status) {
    const tasks = status?.background?.tasks
    if (!tasks || typeof tasks !== 'object') return []
    return Object.keys(tasks).sort().map((name) => ({
        name,
        running: !!tasks[name]?.running,
        last_run_at: tasks[name]?.last_run_at || null,
        last_error_at: tasks[name]?.last_error_at || null,
    }))
}
