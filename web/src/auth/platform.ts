/**
 * Returns true when running inside a Tauri desktop shell.
 * Tauri injects __TAURI_INTERNALS__ into the webview's global scope.
 */
export const isTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

/**
 * Returns true when popup auth is needed instead of redirect.
 * Only production Tauri builds (origin: tauri.localhost) need popups.
 * In dev mode, the WebView loads from localhost:3000 where redirects work fine.
 */
export const needsPopupAuth = (): boolean =>
  isTauri() && window.location.origin.includes('tauri.localhost');

export const getApiBase = (): string => {
  if (import.meta.env.VITE_SURF_API_URL) {
    return import.meta.env.VITE_SURF_API_URL;
  }
  if (isTauri()) {
    return import.meta.env.VITE_DESKTOP_API_URL || '/api/v1';
  }
  return '/api/v1';
};
