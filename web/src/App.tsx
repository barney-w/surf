import React, { useCallback, useEffect, useRef, useState } from "react";
import { ThemeProvider } from "@surf-kit/theme";
import type { ColorMode } from "@surf-kit/theme";
import { ColorModeContext } from "./contexts/ColorModeContext";
import { Button, Drawer, DropdownMenu, IconButton } from "@surf-kit/core";
import { WaveLoader } from "@surf-kit/core";
import { ConversationList } from "@surf-kit/agent/chat";
import type { ChatMessage } from "@surf-kit/agent";
import { History, Settings2 } from "lucide-react";
import { useAuth } from "./auth/AuthProvider";
import { getApiBase } from "./auth/platform";
import { isTauri } from "./auth/platform";
import { ChatPage } from "./pages/ChatPage";
import { SignInPage } from "./pages/SignInPage";
import { ThemeToggle } from "./components/ThemeToggle";
import { DeveloperSettings } from "./components/DeveloperSettings";
import { useConversations } from "./hooks/useConversations";
import { useDeveloperSettings } from "./hooks/useDeveloperSettings";
import { useFeatures } from "./hooks/useFeatures";

const STORAGE_KEY = "surf-color-mode";

function getSavedColorMode(): ColorMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "brand" || saved === "energy")
      return saved;
  } catch {
    /* SSR / private browsing */
  }
  return "brand";
}

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

/** Shape returned by GET /api/v1/chat/:id */
interface ApiMessageRecord {
  id: string;
  role: "user" | "assistant";
  content: string | null;
  agent?: string | null;
  response?: Record<string, unknown> | null;
  attachments?: unknown[];
  timestamp: string;
}

function AppContent() {
  const { isAuthenticated, isGuest, isLoading: authLoading, getApiToken } = useAuth();
  const [chatKey, setChatKey] = useState(0);
  const [hasMessages, setHasMessages] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const features = useFeatures();
  const { conversations, refresh: refreshConversations, deleteConversation } = useConversations({ enabled: features.conversationHistory });
  const { settings: devSettings, updateSetting, resetToDefaults, isDevMode } = useDeveloperSettings();
  const [devSettingsOpen, setDevSettingsOpen] = useState(false);

  // Ref for the loadConversation action exposed by ChatPage
  const loadConversationRef = useRef<((id: string, messages: ChatMessage[]) => void) | null>(null);

  // Ref for the reset action exposed by ChatPage
  const resetChatRef = useRef<(() => void) | null>(null);

  const handleHasMessages = useCallback(
    (has: boolean) => setHasMessages(has),
    [],
  );

  /** Fetch a full conversation from the API and load it into the chat view. */
  const handleSelectConversation = useCallback(
    async (id: string) => {
      try {
        const token = await getApiToken();
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;

        const res = await fetch(`${getApiBase()}/chat/${id}`, {
          credentials: "include",
          headers,
        });
        if (!res.ok) return;

        const data = await res.json();
        const messages: ChatMessage[] = (data.messages ?? []).map(
          (m: ApiMessageRecord) => ({
            id: m.id,
            role: m.role,
            content: m.content ?? "",
            agent: m.agent ?? undefined,
            response: m.response ?? undefined,
            timestamp: new Date(m.timestamp),
          }),
        );

        // If loadConversation ref is available, load into existing instance;
        // otherwise bump the key to create a fresh ChatPage.
        if (loadConversationRef.current) {
          loadConversationRef.current(id, messages);
        }

        setActiveConversationId(id);
        setHasMessages(messages.length > 0);
      } finally {
        setDrawerOpen(false);
      }
    },
    [getApiToken],
  );

  const handleNewChat = useCallback(() => {
    setChatKey((k) => k + 1);
    setHasMessages(false);
    setActiveConversationId(null);
    setDrawerOpen(false);
  }, []);

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      await deleteConversation(id);
      // If we deleted the active conversation, reset the chat
      if (id === activeConversationId) {
        setChatKey((k) => k + 1);
        setHasMessages(false);
        setActiveConversationId(null);
      }
    },
    [deleteConversation, activeConversationId],
  );

  /** Called by ChatPage after a message exchange completes. */
  const handleStreamComplete = useCallback(() => {
    void refreshConversations();
  }, [refreshConversations]);

  /** Called by ChatPage to register its loadConversation action. */
  const handleRegisterActions = useCallback(
    (load: (id: string, msgs: ChatMessage[]) => void, reset: () => void) => {
      loadConversationRef.current = load;
      resetChatRef.current = reset;
    },
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
      <header className="relative flex flex-wrap items-center gap-2 sm:gap-3 px-3 sm:px-6 py-2 sm:py-3 border-b border-border shrink-0">
        {features.conversationHistory && (
          <IconButton
            intent="ghost"
            size="sm"
            aria-label="Conversation history"
            onPress={() => {
              void refreshConversations();
              setDrawerOpen(true);
            }}
          >
            <History size={18} />
          </IconButton>
        )}
        <h1 className="font-display text-lg font-semibold text-text-primary tracking-tight">
          Surf
        </h1>
        <h4 className="absolute inset-x-0 text-center font-display text-xs font-semibold text-text-primary tracking-tight pointer-events-none hidden sm:block">
          Responses are AI-generated.
        </h4>
        <div className="flex-1" />
        <Button
          intent="ghost"
          size="sm"
          aria-label="New chat"
          isDisabled={!hasMessages}
          onPress={handleNewChat}
          className="gap-1.5"
        >
          <NewChatIcon />
          <span className="hidden sm:inline text-sm font-medium">New chat</span>
        </Button>
        {isDevMode && (
          <button
            onClick={() => setDevSettingsOpen(true)}
            aria-label="Developer settings"
            className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface transition-colors cursor-pointer"
          >
            <Settings2 size={18} />
          </button>
        )}
        <ThemeToggle />
        <div className="w-px h-5 bg-border mx-1 sm:mx-4" />
        <UserMenu />
      </header>
      <p className="sm:hidden text-center font-display text-xs font-semibold text-text-primary tracking-tight py-1.5 border-b border-border shrink-0">
        Responses are AI-generated.
      </p>
      {features.conversationHistory && (
        <Drawer
          isOpen={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          side="left"
          title="Conversation history"
          className="flex flex-col px-0 pb-0"
        >
          <ConversationList
            conversations={conversations}
            activeId={activeConversationId ?? undefined}
            onSelect={(id) => void handleSelectConversation(id)}
            onDelete={(id) => void handleDeleteConversation(id)}
            className="-mt-1"
          />
        </Drawer>
      )}
      {isDevMode && (
        <DeveloperSettings
          isOpen={devSettingsOpen}
          onClose={() => setDevSettingsOpen(false)}
          settings={devSettings}
          onUpdate={updateSetting}
          onReset={resetToDefaults}
        />
      )}
      <main className="flex-1 overflow-hidden">
        <ChatPage
          key={chatKey}
          onHasMessages={handleHasMessages}
          onStreamComplete={handleStreamComplete}
          onRegisterActions={handleRegisterActions}
        />
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
      const order: ColorMode[] = ["brand", "light", "dark", "energy"];
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
