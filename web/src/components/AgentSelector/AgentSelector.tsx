import type { AgentWithAccess } from "./useAgentAccess";
import { AgentCard } from "./AgentCard";

type AgentSelectorProps = {
  agents: AgentWithAccess[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSignInPrompt?: () => void;
  className?: string;
};

export function AgentSelector({
  agents,
  selectedId,
  onSelect,
  onSignInPrompt,
  className = "",
}: AgentSelectorProps) {
  return (
    <div
      className={`flex gap-3 overflow-x-auto pb-2 snap-x ${className}`}
      style={{
        scrollbarWidth: "none",
        WebkitOverflowScrolling: "touch",
      }}
    >
      {agents.map((agent, index) => (
        <div
          key={agent.id}
          className="snap-start min-w-[140px] max-w-[180px] flex-shrink-0"
        >
          <AgentCard
            agent={agent}
            selected={selectedId === agent.id}
            onSelect={onSelect}
            onSignInPrompt={onSignInPrompt}
            index={index}
          />
        </div>
      ))}
    </div>
  );
}
