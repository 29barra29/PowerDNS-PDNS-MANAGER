import { parseLuaContent } from '../../lib/luaRecord.js'

// Wert eines LUA-Records in der Tabelle (F15 6.5-4): Ziel-Typ als kleines Badge, daneben der Lua-Code ohne die
// umschliessenden Anfuehrungszeichen. Unparsebarer Inhalt (z. B. extern angelegt) erscheint unveraendert.
export default function LuaValue({ content }) {
    const p = parseLuaContent(content)
    if (!p.parsed) return <>{content}</>
    return (
        <span className="inline-flex items-start gap-0">
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-bg-secondary border border-border mr-2 shrink-0 font-sans">
                {p.rtype}
            </span>
            <code className="font-mono text-xs whitespace-pre-wrap break-all">{p.code}</code>
        </span>
    )
}
