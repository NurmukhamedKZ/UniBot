import WebApp from '@twa-dev/sdk'
import { postEvent, retrieveRawInitData } from '@telegram-apps/bridge'

/**
 * The webapp uses a fixed light brand palette (see src/index.css) instead
 * of following the client's Telegram theme, so instead of copying
 * themeParams onto CSS vars we sync Telegram's own chrome (header and
 * background) to the brand cream. Clients that reject custom hex colors
 * simply keep their default chrome; the page itself is unaffected.
 */
const BRAND_CANVAS = '#f7f7f4'

function applyTheme(): void {
  try {
    WebApp.setBackgroundColor(BRAND_CANVAS)
    WebApp.setHeaderColor(BRAND_CANVAS)
  } catch {
    // Older clients: keep default Telegram chrome.
  }
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
 * @twa-dev/sdk (unmaintained, last published mid-2025) bundles its own stale
 * reimplementation of telegram-web-app.js that leaves `WebApp.initData`
 * undefined on current Telegram clients. `@telegram-apps/bridge` is the
 * actively maintained package and its `retrieveRawInitData` tries multiple
 * sources in order (location.href, then the Performance Navigation Timing
 * API entry -- which keeps the original launch URL even after some library
 * mutates `location.hash` via `history.replaceState` -- then localStorage
 * for reloads), which is what actually makes this reliable.
 */
export function getInitData(): string {
  return retrieveRawInitData() ?? ''
}

/**
 * Same @twa-dev/sdk staleness as initData (see getInitData above):
 * `WebApp.openLink` goes through its bundled bridge reimplementation and can
 * silently no-op on current Telegram clients. `postEvent` talks to the real
 * native bridge directly. Falls back to `window.open` outside Telegram (e.g.
 * testing the Vercel URL in a plain browser tab).
 */
export function openExternalLink(url: string): void {
  try {
    postEvent('web_app_open_link', { url })
  } catch {
    window.open(url, '_blank')
  }
}

export { WebApp }
