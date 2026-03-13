import React, { createContext, useCallback, useEffect, useState } from "react";
import { ThemeProvider } from "@surf-kit/theme";
import type { ColorMode } from "@surf-kit/theme";
import { Button, DropdownMenu } from "@surf-kit/core";
import { WaveLoader } from "@surf-kit/core";
import { useAuth } from "./auth/AuthProvider";
import { isTauri } from "./auth/platform";
import { ChatPage } from "./pages/ChatPage";
import { SignInPage } from "./pages/SignInPage";
import { ThemeToggle } from "./components/ThemeToggle";

const STORAGE_KEY = "surf-color-mode";

function getSavedColorMode(): ColorMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "brand")
      return saved;
  } catch {
    /* SSR / private browsing */
  }
  return "brand";
}

export const ColorModeContext = createContext<{
  colorMode: ColorMode;
  toggleColorMode: () => void;
}>({ colorMode: "brand", toggleColorMode: () => {} });

function SignInButton() {
  const { login } = useAuth();
  return (
    <button
      onClick={login}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium
                 text-accent border border-accent/30 hover:border-accent hover:bg-accent-subtle
                 transition-all duration-150 cursor-pointer active:scale-[0.97]"
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
        <polyline points="10 17 15 12 10 7" />
        <line x1="15" y1="12" x2="3" y2="12" />
      </svg>
      Sign in
    </button>
  );
}

function UserMenu() {
  const { isAuthenticated, isGuest, profile, photoUrl, logout } = useAuth();

  if (!isAuthenticated || !profile) {
    return isGuest ? <SignInButton /> : null;
  }

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
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
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
  const { isAuthenticated, isGuest, isLoading: authLoading } = useAuth();
  const [chatKey, setChatKey] = useState(0);
  const [hasMessages, setHasMessages] = useState(false);
  const handleHasMessages = useCallback(
    (has: boolean) => setHasMessages(has),
    [],
  );

  useEffect(() => {
    if (!isTauri()) return;
    import("@tauri-apps/plugin-updater")
      .then(({ check }) => {
        check()
          .then((update) => {
            if (update) {
              if (
                window.confirm(
                  `Version ${update.version} is available. Update now?`,
                )
              ) {
                void update.downloadAndInstall();
              }
            }
          })
          .catch(() => {
            // Silent fail — don't block app usage
          });
      })
      .catch(() => {
        // Plugin not available
      });
  }, []);

  const authRequired = !!import.meta.env.VITE_ENTRA_CLIENT_ID;
  const needsSignIn = authRequired && !isAuthenticated && !isGuest;

  if (authLoading) {
    return (
      <div className="flex items-center justify-center h-full bg-canvas">
        <WaveLoader size="md" color="#38bdf8" />
      </div>
    );
  }

  if (needsSignIn) {
    return <SignInPage />;
  }

  return (
    <div className="flex flex-col h-full bg-canvas">
      <OfflineBanner />
      <header className="relative flex items-center gap-3 px-6 py-3 border-b border-border shrink-0">
        <h1 className="font-display text-lg font-semibold text-text-primary tracking-tight flex-1">
          Surf
        </h1>
        <h4 className="absolute inset-x-0 text-center font-display text-xs font-semibold text-text-primary tracking-tight pointer-events-none">
          Responses are AI-generated.
        </h4>
        <Button
          intent="secondary"
          size="sm"
          aria-label="New chat"
          isDisabled={!hasMessages}
          onPress={() => {
            setChatKey((k) => k + 1);
            setHasMessages(false);
          }}
          className={`gap-1.5 ${hasMessages ? "transition-colors duration-150 border-accent/40 text-accent hover:border-accent hover:bg-accent-subtle active:scale-[0.98]" : "border-transparent text-text-muted cursor-default"}`}
        >
          <NewChatIcon />
          <span className="hidden sm:inline text-sm font-medium">New chat</span>
        </Button>
        <ThemeToggle />
        <div className="w-px h-5 bg-border mx-4" />
        <UserMenu />
      </header>
      <main className="flex-1 overflow-hidden">
        <ChatPage key={chatKey} onHasMessages={handleHasMessages} />
      </main>
    </div>
  );
}

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Uncaught error:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <h1 className="text-xl font-semibold mb-2">Something went wrong</h1>
            <button
              className="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function App() {
  const [colorMode, setColorMode] = useState<ColorMode>(getSavedColorMode);

  const toggleColorMode = useCallback(() => {
    setColorMode((prev) => {
      const order: ColorMode[] = ["brand", "light", "dark"];
      const next = order[(order.indexOf(prev) + 1) % order.length];
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* noop */
      }
      return next;
    });
  }, []);

  return (
    <ErrorBoundary>
      <ColorModeContext.Provider value={{ colorMode, toggleColorMode }}>
        <ThemeProvider colorMode={colorMode} className="h-full">
          <AppContent />
        </ThemeProvider>
      </ColorModeContext.Provider>
    </ErrorBoundary>
  );
}
