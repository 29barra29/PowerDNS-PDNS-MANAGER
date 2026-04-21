import { useEffect, useRef, forwardRef, useImperativeHandle } from 'react'

/**
 * Captcha-Widget fuer Cloudflare Turnstile, hCaptcha und Google reCAPTCHA v2.
 *
 * Alle drei Provider haben praktisch identische APIs:
 *   window.<sdk>.render(container, { sitekey, theme, callback, 'expired-callback', 'error-callback' }) -> widgetId
 *   window.<sdk>.reset(widgetId)
 *
 * Das macht den Wrapper hier sehr klein - der Parent muss nur Provider + SiteKey
 * setzen und bekommt im onToken-Callback ein Token zurueck, das er dann mit dem
 * Form-Submit ans Backend schickt.
 *
 * Props:
 *   - provider:  'turnstile' | 'hcaptcha' | 'recaptcha' | 'none'
 *   - siteKey:   public site key vom Provider
 *   - theme:     'light' | 'dark' (Default 'dark', passt zum App-Look)
 *   - onToken(token):   Callback wenn der User das Captcha geloest hat
 *   - onExpire():       Callback wenn das Token abgelaufen ist
 *   - onError():        Callback wenn das Widget einen Fehler hatte
 *
 * Imperative ref:
 *   captchaRef.current.reset()  setzt das Widget zurueck (z.B. nach Login-Fehler)
 */

// Konfiguration pro Provider: Script-URL, globaler Object-Name, CSS-Markerklasse.
const PROVIDER_CONFIG = {
    turnstile: {
        scriptSrc: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit',
        sdkObject: 'turnstile',
        scriptId: 'cf-turnstile-script',
    },
    hcaptcha: {
        scriptSrc: 'https://js.hcaptcha.com/1/api.js?render=explicit',
        sdkObject: 'hcaptcha',
        scriptId: 'hcaptcha-script',
    },
    recaptcha: {
        scriptSrc: 'https://www.google.com/recaptcha/api.js?render=explicit',
        sdkObject: 'grecaptcha',
        scriptId: 'recaptcha-script',
    },
}

// Laed das Provider-Script einmalig und resolved, sobald window.<sdk> verfuegbar ist.
// Cached pro Provider, damit Login + Register nicht parallel zwei Tags einfuegen.
const sdkPromises = {}

function loadSdk(provider) {
    const cfg = PROVIDER_CONFIG[provider]
    if (!cfg) return Promise.reject(new Error(`Unknown captcha provider: ${provider}`))
    if (sdkPromises[provider]) return sdkPromises[provider]

    sdkPromises[provider] = new Promise((resolve, reject) => {
        // SDK schon da? (z.B. wenn das Script ueber index.html mitgeladen wurde)
        if (window[cfg.sdkObject] && typeof window[cfg.sdkObject].render === 'function') {
            resolve(window[cfg.sdkObject])
            return
        }

        // Script schon im DOM? Dann nur auf 'load' warten.
        let tag = document.getElementById(cfg.scriptId)
        if (!tag) {
            tag = document.createElement('script')
            tag.id = cfg.scriptId
            tag.src = cfg.scriptSrc
            tag.async = true
            tag.defer = true
            document.head.appendChild(tag)
        }

        // reCAPTCHA + hCaptcha + Turnstile haben unterschiedliche Ready-Patterns.
        // Polling ist hier deutlich einfacher als der jeweils richtige onload-Callback.
        const start = Date.now()
        const tick = () => {
            const sdk = window[cfg.sdkObject]
            if (sdk && typeof sdk.render === 'function') {
                resolve(sdk)
                return
            }
            if (Date.now() - start > 10000) {
                reject(new Error(`Captcha SDK (${provider}) konnte nicht geladen werden`))
                return
            }
            setTimeout(tick, 100)
        }
        tick()
    })

    return sdkPromises[provider]
}

const CaptchaWidget = forwardRef(function CaptchaWidget(
    { provider, siteKey, theme = 'dark', onToken, onExpire, onError },
    ref,
) {
    const containerRef = useRef(null)
    const widgetIdRef = useRef(null)
    const sdkRef = useRef(null)

    useImperativeHandle(ref, () => ({
        reset() {
            const sdk = sdkRef.current
            const id = widgetIdRef.current
            if (sdk && id !== null && id !== undefined) {
                try { sdk.reset(id) } catch { /* SDK evtl. nicht bereit */ }
            }
        },
    }), [])

    useEffect(() => {
        // Ref am Effect-Start in eine lokale Konstante kopieren - sonst meckert
        // react-hooks/exhaustive-deps zu Recht, dass containerRef.current beim Cleanup
        // schon was anderes sein kann.
        const container = containerRef.current
        if (!provider || provider === 'none' || !siteKey || !container) return

        let cancelled = false
        let localWidgetId = null
        let localSdk = null

        loadSdk(provider).then((sdk) => {
            if (cancelled) return
            localSdk = sdk
            sdkRef.current = sdk
            try {
                localWidgetId = sdk.render(container, {
                    sitekey: siteKey,
                    theme,
                    callback: (token) => { if (!cancelled && typeof onToken === 'function') onToken(token) },
                    'expired-callback': () => { if (!cancelled && typeof onExpire === 'function') onExpire() },
                    'error-callback': () => { if (!cancelled && typeof onError === 'function') onError() },
                })
                widgetIdRef.current = localWidgetId
            } catch (err) {
                console.error('Captcha render failed', err)
                if (typeof onError === 'function') onError()
            }
        }).catch((err) => {
            console.error(err)
            if (typeof onError === 'function') onError()
        })

        return () => {
            cancelled = true
            // Beim Unmount: Widget-Container leeren, damit ein erneutes Render
            // (z.B. Provider-Wechsel) keine doppelten Widgets stapelt.
            if (localSdk && localWidgetId !== null && localWidgetId !== undefined) {
                try { localSdk.reset(localWidgetId) } catch { /* ignore */ }
            }
            container.innerHTML = ''
            widgetIdRef.current = null
            sdkRef.current = null
        }
    }, [provider, siteKey, theme, onToken, onExpire, onError])

    if (!provider || provider === 'none' || !siteKey) return null

    return <div ref={containerRef} className="flex justify-center" />
})

export default CaptchaWidget
