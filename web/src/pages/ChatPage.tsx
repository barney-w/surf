import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAgentChat } from "@surf-kit/agent/hooks";
import type { Attachment } from "@surf-kit/agent/types";
import {
  MessageThread,
  MessageComposer,
  WelcomeScreen,
} from "@surf-kit/agent/chat";
import { StreamingMessage } from "@surf-kit/agent/streaming";
import { ErrorResponse } from "@surf-kit/agent/response";
import { WaveLoader } from "@surf-kit/core";
import { useAuth } from "../auth/AuthProvider";
import { getApiBase } from "../auth/platform";

const SUGGESTED_QUESTIONS = [
  "What's the leave policy?",
  "How do I reset my password?",
  "What IT equipment can I request?",
];

/* ------------------------------------------------------------------ */
/*  Sign-in gate — shown in place of the composer when not authed      */
/* ------------------------------------------------------------------ */

function SignInGate() {
  const { login } = useAuth();

  return (
    <div className="w-full max-w-[640px] mx-auto anim-fade-up">
      <div className="glass-panel px-6 py-5 flex flex-col items-center gap-4 text-center">
        <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-accent"
          >
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </div>

        <div>
          <p className="text-text-primary font-display font-semibold text-base">
            Sign in to start chatting
          </p>
          <p className="text-text-secondary text-sm mt-1 max-w-xs">
            Authenticate with your organisation account to ask questions and get
            personalised answers.
          </p>
        </div>

        <button
          onClick={login}
          className="group relative mt-1 px-6 py-2.5 rounded-xl font-display font-semibold text-sm
                     bg-accent text-white
                     hover:bg-accent-hover active:scale-[0.97]
                     transition-all duration-200 cursor-pointer
                     focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
        >
          <span className="relative z-10 flex items-center gap-2">
            <svg
              width="16"
              height="16"
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
          </span>
        </button>
      </div>
    </div>
  );
}

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
  const getApiTokenRef = useRef(getApiToken);
  getApiTokenRef.current = getApiToken;
  const isAuthenticatedRef = useRef(isAuthenticated);
  isAuthenticatedRef.current = isAuthenticated;

  const getHeaders = useCallback(async (): Promise<Record<string, string>> => {
    if (!isAuthenticatedRef.current) return {};
    const token = await getApiTokenRef.current();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, []);

  return useMemo(
    () => ({
      apiUrl: getApiBase(),
      streamPath: "/chat/stream",
      feedbackPath: "/feedback",
      conversationsPath: "/conversations",
      timeout: 60000,
      headers: getHeaders,
    }),
    [getHeaders],
  );
}

/* ------------------------------------------------------------------ */
/*  ChatPage                                                           */
/* ------------------------------------------------------------------ */

export function ChatPage({
  onHasMessages,
}: {
  onHasMessages?: (has: boolean) => void;
}) {
  const { profile, isLoading: authLoading, isAuthenticated } = useAuth();
  const authRequired = !!import.meta.env.VITE_ENTRA_CLIENT_ID;
  const gated = authRequired && !isAuthenticated;
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

  const handleSend = useCallback(
    (content: string, attachments?: Attachment[]) => {
      shouldScrollRef.current = true;
      void actions.sendMessage(content, attachments);
    },
    [actions],
  );

  // Personalised greeting
  const givenName = profile?.givenName ?? profile?.displayName;
  const welcomeTitle = givenName
    ? `Hi ${givenName}, I'm Surf.`
    : "Hi, I'm Surf.";

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
            <ErrorResponse
              error={state.error}
              onRetry={() => actions.retry()}
            />
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
            title={gated ? "Hi, I'm Surf." : welcomeTitle}
            message={
              gated
                ? "I can coordinate specialist agents to answer your questions — sign in to get started."
                : "I can coordinate specialist agents to answer your questions."
            }
            icon={
              <img
                src="/surf.png"
                alt="Surf"
                className="w-32 h-32 rounded-md"
              />
            }
            suggestedQuestions={gated ? [] : SUGGESTED_QUESTIONS}
            onQuestionSelect={handleSend}
            className="flex-none mb-6"
          />
          {gated ? (
            <SignInGate />
          ) : (
            <div className="w-full max-w-[640px]">
              <MessageComposer
                onSend={handleSend}
                isLoading={state.isLoading}
                placeholder="Ask a question..."
                className="bg-surface border-border"
              />
            </div>
          )}
          <div className="flex-[2]" />
        </div>
      )}
    </div>
  );
}
