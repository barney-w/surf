/**
 * Returns true when running inside a Tauri desktop shell.
 * Tauri injects __TAURI_INTERNALS__ into the webview's global scope.
 */
export const isTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export const getApiBase = (): string => {
  if (import.meta.env.VITE_SURF_API_URL) {
    return import.meta.env.VITE_SURF_API_URL;
  }
  if (isTauri()) {
    return import.meta.env.VITE_DESKTOP_API_URL || '/api/v1';
  }
  return '/api/v1';
};
