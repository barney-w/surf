import { useState, useMemo } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { AGENTS, type AgentDef } from "./agentConfig";

const CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad";

export type AgentWithAccess = AgentDef & {
  accessible: boolean;
  lockReason: string | null;
};

export function useAgentAccess() {
  const { isAuthenticated, account } = useAuth();
  const [selectedId, setSelectedId] = useState("coordinator");

  const isOrgAccount = useMemo(() => {
    if (!isAuthenticated || !account) return false;
    const tid = (account.idTokenClaims as Record<string, unknown>)?.tid as string | undefined;
    return !!tid && tid !== CONSUMER_TENANT_ID;
  }, [isAuthenticated, account]);

  const agents: AgentWithAccess[] = useMemo(
    () =>
      AGENTS.map((agent) => {
        if (!agent.enabled) {
          return { ...agent, accessible: false, lockReason: "Coming soon" };
        }
        if (agent.authLevel === "public") {
          return { ...agent, accessible: true, lockReason: null };
        }
        if (agent.authLevel === "microsoft") {
          return isAuthenticated
            ? { ...agent, accessible: true, lockReason: null }
            : { ...agent, accessible: false, lockReason: "Sign in with Microsoft to access" };
        }
        // organisational
        return isOrgAccount
          ? { ...agent, accessible: true, lockReason: null }
          : { ...agent, accessible: false, lockReason: "Requires an organisational account" };
      }),
    [isAuthenticated, isOrgAccount],
  );

  const selectedAgent = agents.find((a) => a.id === selectedId) ?? agents[0];

  return { agents, selectedAgent, setSelectedAgent: setSelectedId };
}
