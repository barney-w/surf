/**
 * Persists MSAL token cache to Tauri's app data directory via tauri-plugin-store.
 *
 * WKWebView (macOS) and some WebView2 configurations can lose localStorage
 * between app restarts. This module mirrors MSAL's localStorage entries into a
 * durable JSON store file, and restores them on startup before MSAL initialises.
 */
import { isTauri } from "./platform";

const STORE_FILE = "auth-cache.json";
const MSAL_KEY_PREFIX = "msal.";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let storeInstance: any = null;

async function getStore() {
  if (storeInstance) return storeInstance;
  // Dynamic import — only resolves inside a Tauri WebView where the IPC
  // bridge exists.  The module path must be a plain string literal so Vite
  // can apply the optimizeDeps.exclude rule.
  const { load } = await import("@tauri-apps/plugin-store");
  storeInstance = await load(STORE_FILE, { autoSave: true, defaults: {} });
  return storeInstance;
}

/**
 * Restore MSAL cache entries from the Tauri store into localStorage.
 * Call this BEFORE creating the MSAL PublicClientApplication.
 */
export async function restoreMsalCache(): Promise<void> {
  if (!isTauri()) return;
  try {
    const store = await getStore();
    const keys = await store.keys();
    for (const key of keys) {
      if (localStorage.getItem(key) === null) {
        const value = await store.get(key);
        if (value != null) {
          localStorage.setItem(key, value);
        }
      }
    }
  } catch {
    // Store not available — fall back to localStorage only
  }
}

/**
 * Snapshot current MSAL cache entries from localStorage into the Tauri store.
 * Call this after any successful login or token acquisition.
 */
export async function persistMsalCache(): Promise<void> {
  if (!isTauri()) return;
  try {
    const store = await getStore();
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(MSAL_KEY_PREFIX)) {
        const value = localStorage.getItem(key);
        if (value != null) {
          await store.set(key, value);
        }
      }
    }
    await store.save();
  } catch {
    // Best-effort persistence
  }
}

/**
 * Clear persisted MSAL cache (call on logout).
 */
export async function clearMsalCache(): Promise<void> {
  if (!isTauri()) return;
  try {
    const store = await getStore();
    await store.clear();
    await store.save();
  } catch {
    // Best-effort
  }
}
