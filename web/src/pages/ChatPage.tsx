import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAgentChat } from "@surf-kit/agent/hooks";
import type { Attachment } from "@surf-kit/agent/types";
import type { ChatMessage } from "@surf-kit/agent";
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
import { BackgroundSlideshow } from "../components/BackgroundSlideshow";
import { AgentSelectorModal, useAgentAccess, AGENT_MESSAGES, AGENT_QUESTIONS } from "../components/AgentSelector";
import { useDeveloperSettings } from "../hooks/useDeveloperSettings";
import { AnalysisPanel } from "../components/AnalysisPanel";
import type { AnalysisData, DebugData, UsageData } from "../components/AnalysisPanel";

/* ------------------------------------------------------------------ */
/*  Typewriter effect for agent welcome message                        */
/* ------------------------------------------------------------------ */

function useTypewriter(text: string, charDelay = 25) {
  const [displayed, setDisplayed] = useState(text);
  const [typing, setTyping] = useState(false);
  const prevText = useRef(text);

  useEffect(() => {
    if (text === prevText.current) return;
    prevText.current = text;
    setDisplayed("");
    setTyping(true);
    let i = 0;
    const id = setInterval(() => {
      i++;
      setDisplayed(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(id);
        setTyping(false);
      }
    }, charDelay);
    return () => clearInterval(id);
  }, [text, charDelay]);

  return { displayed, typing };
}

/* ------------------------------------------------------------------ */
/*  Token-aware chat config                                            */
/* ------------------------------------------------------------------ */

function useChatConfig(extraHeaders: Record<string, string> = {}) {
  const { getApiToken, isAuthenticated, isGuest } = useAuth();
  const getApiTokenRef = useRef(getApiToken);
  getApiTokenRef.current = getApiToken;
  const isAuthenticatedRef = useRef(isAuthenticated);
  isAuthenticatedRef.current = isAuthenticated;
  const isGuestRef = useRef(isGuest);
  isGuestRef.current = isGuest;

  // Keep a stable ref so the callback doesn't re-create on every render
  const extraHeadersRef = useRef(extraHeaders);
  extraHeadersRef.current = extraHeaders;

  const getHeaders = useCallback(async (): Promise<Record<string, string>> => {
    const base: Record<string, string> = { ...extraHeadersRef.current };
    if (!isAuthenticatedRef.current && !isGuestRef.current) return base;
    const token = await getApiTokenRef.current();
    if (token) base.Authorization = `Bearer ${token}`;
    return base;
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
/*  Stream adapter that captures debug/usage SSE events                */
/* ------------------------------------------------------------------ */

/**
 * Build a streamAdapter function for useAgentChat that intercepts
 * `debug` and `usage` SSE events while forwarding all others to the
 * hook's standard onEvent handler.
 *
 * The captured data is written into the supplied mutable ref so it
 * survives across renders without triggering re-renders during streaming.
 */
function makeDebugStreamAdapter(
  pendingRef: React.MutableRefObject<{ debug: DebugData | null; usage: UsageData | null }>,
) {
  return async (
    url: string,
    options: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal },
    onEvent: (event: { type: string; [key: string]: unknown }) => void,
  ) => {
    // Reset pending captures for this request
    pendingRef.current = { debug: null, usage: null };

    const response = await fetch(url, {
      method: options.method,
      headers: options.headers,
      body: options.body,
      signal: options.signal,
    });

    if (!response.ok) {
      onEvent({
        type: "error",
        error: {
          code: "API_ERROR",
          message: `HTTP ${response.status}: ${response.statusText}`,
          retryable: response.status >= 500,
        },
      });
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      onEvent({
        type: "error",
        error: { code: "STREAM_ERROR", message: "No response body", retryable: true },
      });
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6).trim();
        if (data === "[DONE]") continue;

        try {
          const event = JSON.parse(data) as { type: string; [key: string]: unknown };

          // Intercept debug and usage events
          if (event.type === "debug") {
            pendingRef.current.debug = event as unknown as DebugData;
          } else if (event.type === "usage") {
            pendingRef.current.usage = event as unknown as UsageData;
          }

          // Forward all events (including debug/usage) to the hook —
          // the hook ignores unknown types but forwarding keeps things clean.
          onEvent(event);
        } catch {
          // Skip malformed events
        }
      }
    }
  };
}

/* ------------------------------------------------------------------ */
/*  ChatPage                                                           */
/* ------------------------------------------------------------------ */

export function ChatPage({
  onHasMessages,
  onStreamComplete,
  onRegisterActions,
}: {
  onHasMessages?: (has: boolean) => void;
  /** Called after a streaming message exchange finishes so the parent can refresh the conversation list. */
  onStreamComplete?: () => void;
  /** Called once on mount so the parent can invoke loadConversation/reset from the sidebar. */
  onRegisterActions?: (
    load: (id: string, messages: ChatMessage[]) => void,
    reset: () => void,
  ) => void;
}) {
  const { profile, isLoading: authLoading, login } = useAuth();
  const { agents, selectedAgent, setSelectedAgent } = useAgentAccess();
  const agentMessage = AGENT_MESSAGES[selectedAgent.id] ?? AGENT_MESSAGES.coordinator;
  const { displayed: typedMessage, typing } = useTypewriter(agentMessage);
  const suggestedQuestions = AGENT_QUESTIONS[selectedAgent.id] ?? AGENT_QUESTIONS.coordinator;
  const { debugHeaders, isDevMode, settings } = useDeveloperSettings();
  const chatConfig = useChatConfig(debugHeaders);

  // Mutable ref for the stream adapter to write captured debug/usage data into
  const pendingAnalysisRef = useRef<{ debug: DebugData | null; usage: UsageData | null }>({
    debug: null,
    usage: null,
  });

  // Stable stream adapter (only created once)
  const streamAdapter = useMemo(
    () => (isDevMode ? makeDebugStreamAdapter(pendingAnalysisRef) : undefined),
    [isDevMode],
  );

  const configWithAgent = useMemo(
    () => ({
      ...chatConfig,
      bodyExtra: selectedAgent.id === "coordinator" ? undefined : { agent: selectedAgent.id },
      ...(streamAdapter ? { streamAdapter } : {}),
    }),
    [chatConfig, selectedAgent.id, streamAdapter],
  );
  const { state, actions } = useAgentChat(configWithAgent);
  const [isDraining, setIsDraining] = useState(false);
  const hasMessages = state.messages.length > 0;
  const showStreaming = state.isLoading || isDraining;
  const threadWrapperRef = useRef<HTMLDivElement>(null);
  const shouldScrollRef = useRef(false);

  // Committed analysis data — promoted from pendingAnalysisRef when streaming completes
  const [analysisData, setAnalysisData] = useState<AnalysisData | null>(null);

  // Register loadConversation and reset with the parent (App) so the
  // history sidebar can load past conversations or start a new one.
  useEffect(() => {
    onRegisterActions?.(actions.loadConversation, actions.reset);
  }, [actions.loadConversation, actions.reset, onRegisterActions]);

  // Notify the parent when a streaming exchange finishes so the
  // conversation list can be refreshed with the latest titles.
  // Also promote pending analysis data to state.
  const prevLoadingRef = useRef(state.isLoading);
  useEffect(() => {
    if (prevLoadingRef.current && !state.isLoading) {
      onStreamComplete?.();

      // Promote captured debug/usage data into React state
      const pending = pendingAnalysisRef.current;
      if (pending.debug || pending.usage) {
        setAnalysisData({ debug: pending.debug, usage: pending.usage });
      } else {
        setAnalysisData(null);
      }
    }
    prevLoadingRef.current = state.isLoading;
  }, [state.isLoading, onStreamComplete]);

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
      // Clear previous analysis data when a new message is sent
      setAnalysisData(null);
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
    <div className="flex flex-col h-full max-w-[860px] mx-auto px-2 sm:px-4 relative overflow-hidden pb-[env(safe-area-inset-bottom)]">
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

          {/* Analysis panel — only shown in dev mode after stream completes */}
          {isDevMode && analysisData && !showStreaming && (
            <div className="px-4 pb-2">
              <AnalysisPanel data={analysisData} settings={settings} />
            </div>
          )}

          {state.error && (
            <ErrorResponse
              error={state.error}
              onRetry={() => actions.retry()}
            />
          )}

          <div className="shrink-0 py-2 sm:py-3">
            <MessageComposer
              onSend={handleSend}
              onStop={actions.stop}
              isLoading={state.isLoading}
              placeholder="Ask a question..."
              className="bg-surface border-border"
            />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center min-h-0 overflow-y-auto">
          <div className="flex-[1] sm:flex-[3]" />
          <WelcomeScreen
            title={welcomeTitle}
            message={
              <span>
                {typedMessage}
                {typing && <span className="typewriter-cursor" />}
              </span>
            }
            icon={
              <div className="pb-6">
                <AgentSelectorModal
                  agents={agents}
                  selectedAgent={selectedAgent}
                  onSelect={setSelectedAgent}
                  onSignInPrompt={login}
                />
              </div>
            }
            suggestedQuestions={suggestedQuestions}
            onQuestionSelect={handleSend}
            className="flex-none mb-4 sm:mb-6"
          />
          <div className="w-full max-w-[640px] shrink-0">
            <MessageComposer
              onSend={handleSend}
              onStop={actions.stop}
              isLoading={state.isLoading}
              placeholder="Ask a question..."
              className="bg-surface border-border"
            />
          </div>
          <div className="flex-[1] sm:flex-[2]" />
        </div>
      )}
    </div>
  );
}
