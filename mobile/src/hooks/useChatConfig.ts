import { useMemo } from "react";

export function useChatConfig() {
  return useMemo(
    () => ({
      apiUrl: process.env.EXPO_PUBLIC_API_URL || "http://localhost:8090/api/v1",
      streamPath: "/chat/stream",
      feedbackPath: "/feedback",
      conversationsPath: "/conversations",
      timeout: 60000,
      headers: undefined as Record<string, string> | undefined,
    }),
    [],
  );
}
