import { useCallback, useMemo, useSyncExternalStore } from "react";

/* ------------------------------------------------------------------ */
/*  Developer settings — persisted in localStorage, gated by dev-mode */
/* ------------------------------------------------------------------ */

export interface DeveloperSettings {
  topK: number;
  strongThreshold: number;
  partialThreshold: number;
  enableVector: boolean;
  enableStitching: boolean;
  enableBroadened: boolean;
  enableKeyword: boolean;
  enableRewrite: boolean;
  enableProofread: boolean;
}

const STORAGE_KEY = "surf-dev-settings";
const DEV_MODE_KEY = "dev-mode";

export const DEFAULTS: DeveloperSettings = {
  topK: 15,
  strongThreshold: 0.7,
  partialThreshold: 0.4,
  enableVector: true,
  enableStitching: true,
  enableBroadened: true,
  enableKeyword: true,
  enableRewrite: true,
  enableProofread: true,
};

/* ------------------------------------------------------------------ */
/*  Tiny pub/sub so React re-renders when localStorage changes         */
/* ------------------------------------------------------------------ */

let listeners: Array<() => void> = [];

function emitChange() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners = [...listeners, listener];
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

/* ------------------------------------------------------------------ */
/*  Snapshot helpers                                                    */
/* ------------------------------------------------------------------ */

function getSettingsSnapshot(): DeveloperSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DeveloperSettings>;
      return { ...DEFAULTS, ...parsed };
    }
  } catch {
    /* corrupt data — fall back to defaults */
  }
  return DEFAULTS;
}

function getIsDevMode(): boolean {
  try {
    return localStorage.getItem(DEV_MODE_KEY) === "true";
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  External-store snapshots (must return referentially stable values)  */
/* ------------------------------------------------------------------ */

let cachedSettings: DeveloperSettings = getSettingsSnapshot();
let cachedDevMode: boolean = getIsDevMode();

function settingsSnapshot(): DeveloperSettings {
  const fresh = getSettingsSnapshot();
  if (JSON.stringify(fresh) !== JSON.stringify(cachedSettings)) {
    cachedSettings = fresh;
  }
  return cachedSettings;
}

function devModeSnapshot(): boolean {
  const fresh = getIsDevMode();
  if (fresh !== cachedDevMode) {
    cachedDevMode = fresh;
  }
  return cachedDevMode;
}

/* ------------------------------------------------------------------ */
/*  Hook                                                               */
/* ------------------------------------------------------------------ */

export function useDeveloperSettings() {
  const settings = useSyncExternalStore(subscribe, settingsSnapshot);
  const isDevMode = useSyncExternalStore(subscribe, devModeSnapshot);

  const updateSetting = useCallback(
    <K extends keyof DeveloperSettings>(key: K, value: DeveloperSettings[K]) => {
      const current = getSettingsSnapshot();
      const next = { ...current, [key]: value };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage full — ignore */
      }
      emitChange();
    },
    [],
  );

  const resetToDefaults = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULTS));
    } catch {
      /* ignore */
    }
    emitChange();
  }, []);

  /** Build the X-Surf-Debug header and per-setting override headers. */
  const debugHeaders = useMemo((): Record<string, string> => {
    if (!isDevMode) return {};
    return {
      "X-Surf-Debug": "true",
      "X-Surf-Debug-TopK": String(settings.topK),
      "X-Surf-Debug-StrongThreshold": String(settings.strongThreshold),
      "X-Surf-Debug-PartialThreshold": String(settings.partialThreshold),
      "X-Surf-Debug-EnableVector": String(settings.enableVector),
      "X-Surf-Debug-EnableStitching": String(settings.enableStitching),
      "X-Surf-Debug-EnableBroadened": String(settings.enableBroadened),
      "X-Surf-Debug-EnableKeyword": String(settings.enableKeyword),
      "X-Surf-Debug-EnableRewrite": String(settings.enableRewrite),
      "X-Surf-Debug-EnableProofread": String(settings.enableProofread),
    };
  }, [isDevMode, settings]);

  return { settings, updateSetting, resetToDefaults, isDevMode, debugHeaders };
}
