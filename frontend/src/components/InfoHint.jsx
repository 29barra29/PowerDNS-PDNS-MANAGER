/**
 * Kompakter Hinweis: Kreis mit „i“ + Titel + erklärender Text.
 */
export default function InfoHint({ title, children, className = '' }) {
    return (
        <div
            className={`rounded-xl border border-border/90 bg-bg-secondary/50 p-3 sm:p-4 text-sm text-text-secondary ${className}`}
            role="note"
        >
            <div className="flex gap-3 items-start">
                <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-accent/45 bg-accent/10 text-sm font-bold text-accent-light"
                    aria-hidden
                >
                    i
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                    {title && <p className="font-medium text-text-primary text-sm">{title}</p>}
                    <div className="text-xs sm:text-sm leading-relaxed text-text-muted space-y-2">
                        {children}
                    </div>
                </div>
            </div>
        </div>
    )
}
