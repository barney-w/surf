import type { Config } from "tailwindcss";
import { surfKitPreset } from "@surf-kit/theme";

export default {
  content: [
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
    "../../surf-kit/packages/agent/src/**/*.native.tsx",
    "../../surf-kit/packages/core/src/**/*.native.tsx",
  ],
  presets: [surfKitPreset],
} satisfies Config;
