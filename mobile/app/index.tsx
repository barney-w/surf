import { useCallback, useState } from "react";
import { View, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAgentChat } from "@surf-kit/agent/hooks";
import { MessageThread, MessageComposer, WelcomeScreen } from "@surf-kit/agent/chat";
import { StreamingMessage } from "@surf-kit/agent/streaming";
import { ErrorResponse } from "@surf-kit/agent/response";
import { WaveLoader } from "@surf-kit/core";
import { useChatConfig } from "../src/hooks/useChatConfig";

const SUGGESTED_QUESTIONS = [
  "What's the leave policy?",
  "How do I reset my password?",
  "What IT equipment can I request?",
];

export default function ChatScreen() {
  const chatConfig = useChatConfig();
  const { state, actions } = useAgentChat(chatConfig);
  const [isDraining, setIsDraining] = useState(false);
  const hasMessages = state.messages.length > 0;
  const showStreaming = state.isLoading || isDraining;

  const handleSend = useCallback(
    (content: string) => {
      void actions.sendMessage(content);
    },
    [actions],
  );

  return (
    <SafeAreaView className="flex-1 bg-canvas" edges={["top", "left", "right"]}>
      <KeyboardAvoidingView
        className="flex-1"
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        keyboardVerticalOffset={0}
      >
        {hasMessages ? (
          <View className="flex-1 px-4">
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

            <View className="py-3">
              <MessageComposer
                onSend={handleSend}
                isLoading={state.isLoading}
                placeholder="Ask a question..."
              />
            </View>
          </View>
        ) : (
          <View className="flex-1 items-center justify-center px-4">
            <WelcomeScreen
              title="Hi, I'm Surf."
              message="Ask me anything — I'll coordinate with my specialist agents to find you the best answer."
              suggestedQuestions={SUGGESTED_QUESTIONS}
              onQuestionSelect={handleSend}
            />
            <View className="w-full max-w-[640px] mt-6">
              <MessageComposer
                onSend={handleSend}
                isLoading={state.isLoading}
                placeholder="Ask a question..."
              />
            </View>
          </View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
