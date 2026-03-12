import { Component, createContext, useCallback, useContext, useState, type PropsWithChildren, type ErrorInfo } from "react";
import { View, Text, Pressable } from "react-native";
import { Slot } from "expo-router";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { ThemeProvider } from "@surf-kit/theme";
import { AuthProvider } from "../src/auth/AuthProvider";
import { Header } from "../src/components/Header";
import { useNetworkStatus } from "../src/hooks/useNetworkStatus";
import "../global.css";

class ErrorBoundary extends Component<PropsWithChildren, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (__DEV__) {
      console.error("[ErrorBoundary]", error, info.componentStack);
    }
  }

  render() {
    if (this.state.error) {
      return (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24, backgroundColor: "#041F26" }}>
          <Text style={{ color: "#fff", fontSize: 20, fontWeight: "600", marginBottom: 8 }}>
            Something went wrong
          </Text>
          <Text style={{ color: "rgba(255,255,255,0.6)", fontSize: 14, textAlign: "center", marginBottom: 24 }}>
            {this.state.error.message}
          </Text>
          <Pressable
            onPress={() => this.setState({ error: null })}
            style={{ backgroundColor: "#38bdf8", paddingHorizontal: 24, paddingVertical: 10, borderRadius: 8 }}
          >
            <Text style={{ color: "#0a3642", fontWeight: "600" }}>Try again</Text>
          </Pressable>
        </View>
      );
    }
    return this.props.children;
  }
}

interface ChatContextValue {
  chatKey: number;
  reportHasMessages: (has: boolean) => void;
}

const ChatContext = createContext<ChatContextValue>({
  chatKey: 0,
  reportHasMessages: () => {},
});

export function useChatContext() {
  return useContext(ChatContext);
}

function OfflineBanner() {
  const { isConnected } = useNetworkStatus();
  if (isConnected) return null;
  return (
    <View className="bg-amber-500/90 py-1.5 px-4">
      <Text className="text-white text-sm text-center">
        You&apos;re offline. Reconnect to continue chatting.
      </Text>
    </View>
  );
}

function AppContent() {
  const insets = useSafeAreaInsets();
  const [chatKey, setChatKey] = useState(0);
  const [hasMessages, setHasMessages] = useState(false);

  const handleNewChat = useCallback(() => {
    setChatKey((prev) => prev + 1);
    setHasMessages(false);
  }, []);

  const reportHasMessages = useCallback((has: boolean) => {
    setHasMessages(has);
  }, []);

  return (
    <AuthProvider>
      <ThemeProvider colorMode="brand">
        <View className="flex-1 flex-col bg-canvas" style={{ paddingTop: insets.top }}>
          <StatusBar style="light" />
          <OfflineBanner />
          <Header
            hasMessages={hasMessages}
            onNewChat={handleNewChat}
          />
          <ChatContext.Provider value={{ chatKey, reportHasMessages }}>
            <Slot />
          </ChatContext.Provider>
        </View>
      </ThemeProvider>
    </AuthProvider>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ErrorBoundary>
        <AppContent />
      </ErrorBoundary>
    </SafeAreaProvider>
  );
}
