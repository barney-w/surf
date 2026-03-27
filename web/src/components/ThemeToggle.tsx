import { useColorMode } from "../contexts/ColorModeContext";
import type { ColorMode } from "@surf-kit/theme";

const icons: Record<ColorMode, React.ReactNode> = {
  brand: (
    // Wave — surf brand identity
    <path d="M2 12c2-3 4-5 7-5 4 0 5 4 9 4 3 0 4-2 4-2" strokeLinecap="round" />
  ),
  light: (
    // Sun
    <>
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </>
  ),
  dark: (
    // Moon
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  ),
  energy: (
    // Lightning bolt
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="none" />
  ),
};

const labels: Record<ColorMode, string> = {
  brand: "Brand theme",
  light: "Light theme",
  dark: "Dark theme",
  energy: "Energy theme",
};

export function ThemeToggle() {
  const { colorMode, toggleColorMode } = useColorMode();

  return (
    <button
      onClick={toggleColorMode}
      aria-label={labels[colorMode]}
      title={labels[colorMode]}
      className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface transition-colors cursor-pointer"
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {icons[colorMode]}
      </svg>
    </button>
  );
}
