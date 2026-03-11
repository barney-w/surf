import { createContext, useCallback, useContext, useState } from "react";
import { View, Text } from "react-native";
import { Slot } from "expo-router";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { ThemeProvider } from "@surf-kit/theme";
import { AuthProvider } from "../src/auth/AuthProvider";
import { Header } from "../src/components/Header";
import { useNetworkStatus } from "../src/hooks/useNetworkStatus";
import "../global.css";

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
        You're offline. Reconnect to continue chatting.
      </Text>
    </View>
  );
}

function AppContent() {
  const insets = useSafeAreaInsets();
  const [colorMode, setColorMode] = useState<"brand" | "light">("brand");
  const [chatKey, setChatKey] = useState(0);
  const [hasMessages, setHasMessages] = useState(false);

  const toggleColorMode = useCallback(() => {
    setColorMode((prev) => (prev === "brand" ? "light" : "brand"));
  }, []);

  const handleNewChat = useCallback(() => {
    setChatKey((prev) => prev + 1);
    setHasMessages(false);
  }, []);

  const reportHasMessages = useCallback((has: boolean) => {
    setHasMessages(has);
  }, []);

  return (
    <AuthProvider>
      <ThemeProvider colorMode={colorMode}>
        <View className="flex-1 flex-col bg-canvas" style={{ paddingTop: insets.top }}>
          <StatusBar style={colorMode === "brand" ? "light" : "dark"} />
          <OfflineBanner />
          <Header
            hasMessages={hasMessages}
            onNewChat={handleNewChat}
            colorMode={colorMode}
            onToggleColorMode={toggleColorMode}
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
      <AppContent />
    </SafeAreaProvider>
  );
}
