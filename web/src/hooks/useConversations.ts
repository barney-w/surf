import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "../auth/AuthProvider";
import { getApiBase } from "../auth/platform";

/** Shape returned by GET /api/v1/conversations (snake_case from the API). */
interface ApiConversationSummary {
  id: string;
  title: string;
  last_message_preview: string | null;
  updated_at: string;
  last_active_agent: string | null;
  message_count: number;
}

/** Normalised summary used by the frontend (camelCase). */
export interface ConversationSummary {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: Date;
  messageCount: number;
}

/** Normalise API response to match @surf-kit/agent ConversationSummary shape. */
function normalise(raw: ApiConversationSummary): ConversationSummary {
  return {
    id: raw.id,
    title: raw.title,
    lastMessage: raw.last_message_preview ?? "",
    updatedAt: new Date(raw.updated_at),
    messageCount: raw.message_count,
  };
}

/**
 * Fetches and manages the list of conversations for the current user.
 *
 * Conversations are fetched from `GET /api/v1/conversations` and normalised
 * into the camelCase shape expected by @surf-kit/agent's ConversationList.
 */
export function useConversations({ enabled = true }: { enabled?: boolean } = {}) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const { getApiToken, isAuthenticated, isGuest } = useAuth();

  // Keep refs to avoid re-creating the callback when auth state changes
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

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const headers = await getHeaders();
      const res = await fetch(`${getApiBase()}/conversations`, {
        credentials: "include",
        headers,
      });
      if (res.ok) {
        const data: ApiConversationSummary[] = await res.json();
        setConversations(data.map(normalise));
      }
    } finally {
      setLoading(false);
    }
  }, [enabled, getHeaders]);

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const deleteConversation = useCallback(
    async (id: string) => {
      const headers = await getHeaders();
      const res = await fetch(`${getApiBase()}/chat/${id}`, {
        method: "DELETE",
        credentials: "include",
        headers,
      });
      if (res.ok) {
        // Remove from local state immediately for snappy UI
        setConversations((prev) => prev.filter((c) => c.id !== id));
      }
    },
    [getHeaders],
  );

  return { conversations, loading, refresh, deleteConversation };
}
