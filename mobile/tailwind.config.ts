import type { Config } from "tailwindcss";
import nativewindPreset from "nativewind/preset";
import { surfKitPreset } from "@surf-kit/theme";

/**
 * Resolved brand-mode colour tokens for React Native.
 *
 * The surfKitPreset defines colours as `var(--surf-*)` CSS custom properties
 * which work on web but don't resolve in React Native (no CSS cascade).
 * We override with concrete hex values from @surf-kit/tokens brand output.
 */
const nativeColors = {
  surface: { DEFAULT: "#0a3642", raised: "#0d3f50", sunken: "#031519" },
  canvas: "#041f26",
  "text-primary": "#f1f0e3",
  "text-secondary": "#f1f0e399",
  "text-muted": "#f1f0e366",
  accent: {
    DEFAULT: "#0091a5",
    hover: "#38bdd0",
    active: "#2aa8bc",
    subtle: "#0091a526",
    subtlest: "#0091a514",
  },
  border: {
    DEFAULT: "#e1b98926",
    strong: "#e1b9894d",
    interactive: "#e1b98980",
  },
  status: {
    success: "#38bdd0",
    "success-subtle": "#38bdd026",
    warning: "#e1b989",
    "warning-subtle": "#e1b9891a",
    error: "#e81152",
    "error-subtle": "#e811521a",
    info: "#0091a5",
    "info-subtle": "#0091a526",
  },
};

export default {
  content: [
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
    "../../surf-kit/packages/agent/src/**/*.native.tsx",
    "../../surf-kit/packages/core/src/**/*.native.tsx",
  ],
  presets: [nativewindPreset, surfKitPreset],
  theme: {
    extend: {
      colors: nativeColors,
    },
  },
} satisfies Config;
