import WebApp from '@twa-dev/sdk'

/**
 * ThemeParams field -> our --tg-* CSS custom property (see src/index.css).
 * Telegram sends hex colors directly, so no conversion is needed -- just
 * copy each one onto :root once WebApp is ready.
 */
const THEME_VAR_MAP: Record<string, string> = {
  bg_color: '--tg-bg',
  secondary_bg_color: '--tg-secondary-bg',
  text_color: '--tg-text',
  hint_color: '--tg-hint',
  link_color: '--tg-link',
  button_color: '--tg-button',
  button_text_color: '--tg-button-text',
  accent_text_color: '--tg-accent',
  section_separator_color: '--tg-border',
}

function applyTheme(): void {
  const root = document.documentElement
  const params = WebApp.themeParams as unknown as Record<string, string>
  for (const [key, cssVar] of Object.entries(THEME_VAR_MAP)) {
    const value = params[key]
    if (value) root.style.setProperty(cssVar, value)
  }
  root.style.colorScheme = WebApp.colorScheme
}

/**
 * Call once on app boot: expands the Mini App to full height and syncs theme.
 *
 * @twa-dev/sdk's `WebApp` export is a snapshot of `window.Telegram.WebApp`
 * taken at import time, but the real client populates that object
 * asynchronously -- `.ready` can be undefined for the first render tick.
 * Calling it synchronously here would throw and abort `main.tsx` before
 * `createRoot().render()` ever runs, leaving a blank screen. Poll until the
 * method actually exists instead of trusting the import-time snapshot.
 */
export function initTelegramWebApp(): void {
  const tryInit = (): void => {
    if (typeof WebApp.ready !== 'function') {
      setTimeout(tryInit, 50)
      return
    }
    WebApp.ready()
    WebApp.expand()
    applyTheme()
    WebApp.onEvent('themeChanged', applyTheme)
  }
  tryInit()
}

/**
 * @twa-dev/sdk bundles its own reimplementation of telegram-web-app.js that
 * parses `location.hash` at module-import time -- in practice (Telegram
 * Desktop 9.6 at least) this leaves `WebApp.initData`/`.version`/`.platform`
 * all `undefined`, not even falling back to the library's own defaults.
 * `location.hash` itself reliably carries the real `tgWebAppData` Telegram
 * put there, so read it directly instead of trusting the SDK's getter.
 */
export function getInitData(): string {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  return params.get('tgWebAppData') ?? WebApp.initData ?? ''
}

export { WebApp }
