import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAgentChat } from "@surf-kit/agent/hooks";
import { MessageThread, MessageComposer, WelcomeScreen } from "@surf-kit/agent/chat";
import { StreamingMessage } from "@surf-kit/agent/streaming";
import { ErrorResponse } from "@surf-kit/agent/response";
import { WaveLoader } from "@surf-kit/core";
import { useAuth } from "../auth/AuthProvider";

const SUGGESTED_QUESTIONS = [
  "What's the leave policy?",
  "How do I reset my password?",
  "What IT equipment can I request?",
];

const BG_IMAGES = [
  "/branding/bg.jpg",
  "/branding/bg2.jpg",
  "/branding/bg3.jpg",
];

/* ------------------------------------------------------------------ */
/*  Background slideshow                                               */
/* ------------------------------------------------------------------ */

function BackgroundSlideshow() {
  const [bgIndex, setBgIndex] = useState(0);

  useEffect(() => {
    BG_IMAGES.forEach((src) => {
      const img = new Image();
      img.src = src;
    });
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setBgIndex((prev) => (prev + 1) % BG_IMAGES.length);
    }, 15000);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      {BG_IMAGES.map((src, i) => (
        <div
          key={src}
          className="fixed inset-0 bg-cover bg-center bg-no-repeat transition-opacity duration-[2000ms] ease-in-out pointer-events-none"
          style={{
            backgroundImage: `url(${src})`,
            opacity: i === bgIndex ? 0.09 : 0,
          }}
        />
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Token-aware chat config                                            */
/* ------------------------------------------------------------------ */

function useChatConfig() {
  const { getApiToken, isAuthenticated } = useAuth();
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) {
      setToken(null);
      return;
    }
    // Acquire token for API calls
    void getApiToken().then(setToken);
  }, [isAuthenticated, getApiToken]);

  return useMemo(
    () => ({
      apiUrl: import.meta.env.VITE_SURF_API_URL || "/api/v1",
      streamPath: "/chat/stream",
      feedbackPath: "/feedback",
      conversationsPath: "/conversations",
      timeout: 60000,
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    }),
    [token],
  );
}

/* ------------------------------------------------------------------ */
/*  ChatPage                                                           */
/* ------------------------------------------------------------------ */

export function ChatPage({ onHasMessages }: { onHasMessages?: (has: boolean) => void }) {
  const { profile, isLoading: authLoading } = useAuth();
  const chatConfig = useChatConfig();
  const { state, actions } = useAgentChat(chatConfig);
  const [isDraining, setIsDraining] = useState(false);
  const hasMessages = state.messages.length > 0;
  const showStreaming = state.isLoading || isDraining;
  const threadWrapperRef = useRef<HTMLDivElement>(null);
  const shouldScrollRef = useRef(false);

  useEffect(() => {
    onHasMessages?.(hasMessages);
  }, [hasMessages, onHasMessages]);

  // Force-scroll when user sends a message
  useEffect(() => {
    if (!shouldScrollRef.current) return;
    shouldScrollRef.current = false;
    requestAnimationFrame(() => {
      const scrollEl = threadWrapperRef.current?.querySelector('[role="log"]');
      if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
    });
  }, [state.messages.length]);

  const handleSend = useCallback((content: string) => {
    shouldScrollRef.current = true;
    void actions.sendMessage(content);
  }, [actions]);

  // Personalised greeting
  const givenName = profile?.givenName ?? profile?.displayName;
  const welcomeTitle = givenName ? `Hi ${givenName}, I'm Surf.` : "Hi, I'm Surf.";

  if (authLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <WaveLoader size="md" color="#38bdf8" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full max-w-[860px] mx-auto px-4 relative overflow-hidden">
      <BackgroundSlideshow />

      {hasMessages ? (
        <div className="flex flex-col flex-1 min-h-0" ref={threadWrapperRef}>
          <MessageThread
            messages={state.messages}
            showAgent
            showSources
            showConfidence={false}
            showVerification={false}
            hideLastAssistant={isDraining}
            streamingSlot={
              showStreaming ? (
                <StreamingMessage
                  stream={{
                    active: state.isLoading,
                    phase: state.streamPhase,
                    content: state.streamingContent,
                    sources: [],
                    agent: state.streamingAgent,
                    agentLabel: null,
                  }}
                  onDraining={setIsDraining}
                />
              ) : undefined
            }
          />

          {state.error && (
            <ErrorResponse error={state.error} onRetry={() => actions.retry()} />
          )}

          <div className="shrink-0 py-3">
            <MessageComposer
              onSend={handleSend}
              isLoading={state.isLoading}
              placeholder="Ask a question..."
              className="bg-surface border-border"
            />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center">
          <div className="flex-[3]" />
          <WelcomeScreen
            title={welcomeTitle}
            message="Ask me anything — I'll coordinate with my specialist agents to find you the best answer."
            icon={
              <img src="/surf.png" alt="Surf" className="w-32 h-30 rounded-md" />
            }
            suggestedQuestions={SUGGESTED_QUESTIONS}
            onQuestionSelect={handleSend}
            className="flex-none mb-6"
          />
          <div className="w-full max-w-[640px]">
            <MessageComposer
              onSend={handleSend}
              isLoading={state.isLoading}
              placeholder="Ask a question..."
              className="bg-surface border-border"
            />
          </div>
          <div className="flex-[2]" />
        </div>
      )}
    </div>
  );
}
