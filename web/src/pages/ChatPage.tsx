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
import { AgentSelectorModal, useAgentAccess, AGENT_MESSAGES, AGENT_QUESTIONS } from "../components/AgentSelector";

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
  const agentMessage = AGENT_MESSAGES[selectedAgent.id] ?? AGENT_MESSAGES.coordinator;
  const { displayed: typedMessage, typing } = useTypewriter(agentMessage);
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
