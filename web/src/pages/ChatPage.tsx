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
import { BackgroundSlideshow } from "../components/BackgroundSlideshow";
import { AgentSelector, AgentIcon, useAgentAccess, AGENT_QUESTIONS } from "../components/AgentSelector";

/* ------------------------------------------------------------------ */
/*  Token-aware chat config                                            */
/* ------------------------------------------------------------------ */

function useChatConfig() {
  const { getApiToken, isAuthenticated, isGuest } = useAuth();
  const getApiTokenRef = useRef(getApiToken);
  getApiTokenRef.current = getApiToken;
  const isAuthenticatedRef = useRef(isAuthenticated);
  isAuthenticatedRef.current = isAuthenticated;
  const isGuestRef = useRef(isGuest);
  isGuestRef.current = isGuest;

  const getHeaders = useCallback(async (): Promise<Record<string, string>> => {
    if (!isAuthenticatedRef.current && !isGuestRef.current) return {};
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
  const { profile, isLoading: authLoading, login } = useAuth();
  const { agents, selectedAgent, setSelectedAgent } = useAgentAccess();
  const suggestedQuestions = AGENT_QUESTIONS[selectedAgent.id] ?? AGENT_QUESTIONS.coordinator;
  const chatConfig = useChatConfig();
  const configWithAgent = useMemo(
    () => ({
      ...chatConfig,
      bodyExtra: selectedAgent.id === "coordinator" ? undefined : { agent: selectedAgent.id },
    }),
    [chatConfig, selectedAgent.id],
  );
  const { state, actions } = useAgentChat(configWithAgent);
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
            <div className="flex items-center gap-2 px-2 pb-1">
              <AgentIcon
                iconName={selectedAgent.iconName}
                size={14}
                style={{ color: `var(${selectedAgent.accentVar})` }}
              />
              <span className="text-xs text-text-secondary font-display">
                {selectedAgent.label}
              </span>
            </div>
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
            message="I can coordinate specialist agents to answer your questions."
            icon={
              <img
                src="/surf.png"
                alt="Surf"
                className="w-32 h-32 rounded-md"
              />
            }
            suggestedQuestions={suggestedQuestions}
            onQuestionSelect={handleSend}
            className="flex-none mb-6"
          />
          <AgentSelector
            agents={agents}
            selectedId={selectedAgent.id}
            onSelect={setSelectedAgent}
            onSignInPrompt={login}
            className="w-full max-w-[640px] mb-4"
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
