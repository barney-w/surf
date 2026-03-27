import { useState, useEffect, useCallback } from "react";
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

  const hasAuth = isAuthenticated || isGuest;

  const refresh = useCallback(async () => {
    if (!enabled || !hasAuth) return;
    setLoading(true);
    try {
      const token = await getApiToken();
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
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
  }, [enabled, hasAuth, getApiToken]);

  // Re-fetch whenever auth state changes (e.g. guest login completes)
  useEffect(() => {
    if (enabled && hasAuth) void refresh();
  }, [enabled, hasAuth, refresh]);

  const deleteConversation = useCallback(
    async (id: string) => {
      const token = await getApiToken();
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
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
    [getApiToken],
  );

  return { conversations, loading, refresh, deleteConversation };
}
