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

/** Call once on app boot: expands the Mini App to full height and syncs theme. */
export function initTelegramWebApp(): void {
  WebApp.ready()
  WebApp.expand()
  applyTheme()
  WebApp.onEvent('themeChanged', applyTheme)
}

export { WebApp }
