// Mini-Quelltext fuer check-locales-Fixtures (E7)
export function demo(t, code, k) {
    const field = { labelKey: 'common.greet' }
    return [t('common.save'), t('items.count', { count: 2 }), t('dyn.err.' + code), t(`items.${k}`), field]
}
