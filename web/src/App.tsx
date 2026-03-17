import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ThemeProvider } from "@surf-kit/theme";
import type { ColorMode } from "@surf-kit/theme";
import { Button, DropdownMenu } from "@surf-kit/core";
import { useAuth } from "./auth/AuthProvider";
import { isTauri } from "./auth/platform";
import { ChatPage } from "./pages/ChatPage";

const STORAGE_KEY = "surf-color-mode";

function getSavedColorMode(): ColorMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "brand") return saved;
  } catch { /* SSR / private browsing */ }
  return "brand";
}

const ColorModeContext = createContext<{
  colorMode: ColorMode;
  toggleColorMode: () => void;
}>({ colorMode: "brand", toggleColorMode: () => {} });

function ThemeToggle() {
  const { colorMode, toggleColorMode } = useContext(ColorModeContext);

  return (
    <button
      onClick={toggleColorMode}
      aria-label={`Switch to ${colorMode === "brand" ? "light" : "brand"} theme`}
      className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface transition-colors cursor-pointer"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {colorMode === "brand" ? (
          /* Sun icon — clicking switches to light */
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
        ) : (
          /* Moon icon — clicking switches to brand/dark */
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        )}
      </svg>
    </button>
  );
}

function UserMenu() {
  const { isAuthenticated, profile, photoUrl, logout } = useAuth();

  if (!isAuthenticated || !profile) return null;

  const initials = (profile.givenName ?? profile.displayName ?? "?")
    .charAt(0)
    .toUpperCase();

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-text-secondary hidden sm:inline">
        {profile.givenName ?? profile.displayName}
      </span>
      <DropdownMenu
        aria-label="User menu"
        align="end"
        trigger={
          <button className="w-8 h-8 rounded-full overflow-hidden border border-border-strong hover:border-accent transition-colors flex items-center justify-center bg-surface text-accent text-sm font-semibold cursor-pointer">
            {photoUrl ? (
              <img
                src={photoUrl}
                alt={profile.displayName}
                className="w-full h-full object-cover"
              />
            ) : (
              initials
            )}
          </button>
        }
        items={[
          { key: "profile", label: `${profile.displayName}`, isDisabled: true },
          ...(profile.department
            ? [{ key: "dept", label: profile.department, isDisabled: true }]
            : []),
          { key: "sign-out", label: "Sign out" },
        ]}
        onAction={(key) => {
          if (key === "sign-out") logout();
        }}
      />
    </div>
  );
}

function SignInButton() {
  const { isAuthenticated, isLoading, login } = useAuth();

  const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
  if (!clientId || isAuthenticated || isLoading) return null;

  return (
    <button
      onClick={login}
      className="text-sm text-text-secondary hover:text-text-primary border border-border-strong hover:border-accent px-3 py-1 rounded-md transition-colors cursor-pointer"
    >
      Sign in
    </button>
  );
}

function NewChatIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M13.5 2.5l-1-1a1.41 1.41 0 0 0-2 0L3 9l-1 4 4-1 7.5-7.5a1.41 1.41 0 0 0 0-2z" />
      <path d="M10 4l2 2" />
    </svg>
  );
}

function OfflineBanner() {
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  if (isOnline) return null;

  return (
    <div className="bg-amber-500/90 text-white text-sm text-center py-1.5 px-4">
      You're offline. Reconnect to continue chatting.
    </div>
  );
}

function AppContent() {
  const [chatKey, setChatKey] = useState(0);
  const [hasMessages, setHasMessages] = useState(false);
  const handleHasMessages = useCallback((has: boolean) => setHasMessages(has), []);

  useEffect(() => {
    if (!isTauri()) return;
    import('@tauri-apps/plugin-updater').then(({ check }) => {
      check().then((update) => {
        if (update) {
          if (window.confirm(`Version ${update.version} is available. Update now?`)) {
            void update.downloadAndInstall();
          }
        }
      }).catch(() => {
        // Silent fail — don't block app usage
      });
    }).catch(() => {
      // Plugin not available
    });
  }, []);

  return (
    <div className="flex flex-col h-full bg-canvas">
      <OfflineBanner />
      <header className="flex items-center gap-3 px-6 py-3 border-b border-border shrink-0">
        <h1 className="font-display text-lg font-semibold text-text-primary tracking-tight flex-1">
          Surf
        </h1>
        <Button
          intent="secondary"
          size="sm"
          aria-label="New chat"
          isDisabled={!hasMessages}
          onPress={() => {
            setChatKey((k) => k + 1);
            setHasMessages(false);
          }}
          className={`gap-1.5 ${hasMessages ? 'transition-colors duration-150 border-accent/40 text-accent hover:border-accent hover:bg-accent-subtle active:scale-[0.98]' : 'border-transparent text-text-muted cursor-default'}`}
        >
          <NewChatIcon />
          <span className="hidden sm:inline text-sm font-medium">New chat</span>
        </Button>
        <ThemeToggle />
        <div className="w-px h-5 bg-border mx-4" />
        <UserMenu />
        <SignInButton />
      </header>
      <main className="flex-1 overflow-hidden">
        <ChatPage key={chatKey} onHasMessages={handleHasMessages} />
      </main>
    </div>
  );
}

export function App() {
  const [colorMode, setColorMode] = useState<ColorMode>(getSavedColorMode);

  const toggleColorMode = useCallback(() => {
    setColorMode((prev) => {
      const next: ColorMode = prev === "brand" ? "light" : "brand";
      try { localStorage.setItem(STORAGE_KEY, next); } catch { /* noop */ }
      return next;
    });
  }, []);

  return (
    <ColorModeContext.Provider value={{ colorMode, toggleColorMode }}>
      <ThemeProvider colorMode={colorMode} className="h-full">
        <AppContent />
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
