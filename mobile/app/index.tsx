import { useCallback, useEffect, useState } from "react";
import { View, Text, Image, KeyboardAvoidingView, Keyboard, Pressable, Platform } from "react-native";
import Animated, { FadeInUp } from "react-native-reanimated";
import * as Haptics from "expo-haptics";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAgentChat } from "@surf-kit/agent/hooks";
import { MessageThread, MessageComposer, WelcomeScreen } from "@surf-kit/agent/chat";
import { StreamingMessage } from "@surf-kit/agent/streaming";
import { ErrorResponse } from "@surf-kit/agent/response";
import { WaveLoader } from "@surf-kit/core";
import { useAuth } from "../src/auth/AuthProvider";
import { useChatConfig } from "../src/hooks/useChatConfig";
import { useNetworkStatus } from "../src/hooks/useNetworkStatus";
import { SignInGate } from "../src/components/SignInGate";
import { BackgroundSlideshow } from "../src/components/BackgroundSlideshow";
import { useChatContext } from "./_layout";

const SUGGESTED_QUESTIONS = [
  "What's the leave policy?",
  "How do I reset my password?",
  "What IT equipment can I request?",
];

function ChatContent() {
  const { profile, isLoading: authLoading, isAuthenticated } = useAuth();
  const chatConfig = useChatConfig();
  const { state, actions } = useAgentChat(chatConfig);
  const { reportHasMessages } = useChatContext();
  const { isConnected } = useNetworkStatus();
  const [isDraining, setIsDraining] = useState(false);
  const hasMessages = state.messages.length > 0;
  const showStreaming = state.isLoading || isDraining;

  const authRequired = !!process.env.EXPO_PUBLIC_ENTRA_CLIENT_ID;
  const gated = authRequired && !isAuthenticated;

  const givenName = profile?.givenName ?? profile?.displayName;
  const welcomeTitle = givenName ? `Hi ${givenName}, I'm Surf.` : "Hi, I'm Surf.";

  useEffect(() => {
    reportHasMessages(hasMessages);
  }, [hasMessages, reportHasMessages]);

  const handleSend = useCallback(
    (content: string) => {
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      Keyboard.dismiss();
      void actions.sendMessage(content);
    },
    [actions],
  );

  if (authLoading) {
    return (
      <SafeAreaView className="flex-1 bg-canvas items-center justify-center" edges={["top", "left", "right"]}>
        <WaveLoader size="md" color="#38bdf8" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView className="flex-1 bg-canvas" edges={["top", "left", "right"]}>
      <BackgroundSlideshow />
      <KeyboardAvoidingView
        className="flex-1"
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        keyboardVerticalOffset={0}
      >
        {hasMessages ? (
          <Pressable className="flex-1 max-w-[860px] self-center w-full px-4" onPress={Keyboard.dismiss}>
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

            {!isConnected && (
              <View className="mb-2 p-3 rounded-lg bg-status-warning-subtle">
                <Text className="text-status-warning text-sm text-center">
                  You're offline. Messages can't be sent right now.
                </Text>
              </View>
            )}

            <View className="shrink-0 py-3">
              <MessageComposer
                onSend={handleSend}
                isLoading={state.isLoading || !isConnected}
                placeholder="Ask a question..."
              />
            </View>
          </Pressable>
        ) : (
          <View className="flex-1 flex-col items-center px-4">
            <View className="flex-[3]" />
            <Animated.View entering={FadeInUp.duration(500).springify()}>
            <WelcomeScreen
              title={gated ? "Hi, I'm Surf." : welcomeTitle}
              message={gated
                ? "I can coordinate specialist agents to answer your questions — sign in to get started."
                : "Ask me anything — I'll coordinate with my specialist agents to find you the best answer."
              }
              icon={<Image source={require('../assets/surf.png')} style={{ width: 128, height: 120, borderRadius: 6 }} />}
              suggestedQuestions={gated ? [] : SUGGESTED_QUESTIONS}
              onQuestionSelect={handleSend}
              className="flex-none mb-6"
            />
            </Animated.View>
            {gated ? (
              <SignInGate />
            ) : (
              <>
                {!isConnected && (
                  <View className="w-full max-w-[640px] mb-4 p-3 rounded-lg bg-status-warning-subtle">
                    <Text className="text-status-warning text-sm text-center">
                      You're offline. Messages can't be sent right now.
                    </Text>
                  </View>
                )}
                <View className="w-full max-w-[640px]">
                  <MessageComposer
                    onSend={handleSend}
                    isLoading={state.isLoading || !isConnected}
                    placeholder="Ask a question..."
                  />
                </View>
              </>
            )}
            <View className="flex-[2]" />
          </View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

export default function ChatScreen() {
  const { chatKey } = useChatContext();
  return <ChatContent key={chatKey} />;
}
