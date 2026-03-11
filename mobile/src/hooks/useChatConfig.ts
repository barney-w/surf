import { useCallback, useMemo, useRef } from "react";
import { useAuth } from "../auth/AuthProvider";
import { getApiUrl } from "../utils/apiUrl";
import { createNativeSSEAdapter } from "../utils/sseAdapter";

export function useChatConfig() {
  const { getApiToken, isAuthenticated } = useAuth();

  // Use refs to avoid stale closures — the async getHeaders function
  // captures the ref, so it always reads the latest value.
  const getApiTokenRef = useRef(getApiToken);
  getApiTokenRef.current = getApiToken;
  const isAuthenticatedRef = useRef(isAuthenticated);
  isAuthenticatedRef.current = isAuthenticated;

  const getHeaders = useCallback(async (): Promise<Record<string, string>> => {
    if (!isAuthenticatedRef.current) return {};
    const token = await getApiTokenRef.current();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, []);

  // Stable reference — the adapter is stateless so we only create it once
  const streamAdapter = useRef(createNativeSSEAdapter()).current;

  return useMemo(
    () => ({
      apiUrl: getApiUrl(),
      streamPath: "/chat/stream",
      feedbackPath: "/feedback",
      conversationsPath: "/conversations",
      timeout: 60000,
      headers: getHeaders,
      streamAdapter,
    }),
    [getHeaders, streamAdapter],
  );
}
