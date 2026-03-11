import type { Config } from "tailwindcss";
import nativewindPreset from "nativewind/preset";
import { surfKitPreset } from "@surf-kit/theme";

export default {
  content: [
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
    "../../surf-kit/packages/agent/src/**/*.native.tsx",
    "../../surf-kit/packages/core/src/**/*.native.tsx",
  ],
  presets: [nativewindPreset, surfKitPreset],
} satisfies Config;
