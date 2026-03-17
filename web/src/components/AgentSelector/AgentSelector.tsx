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
    <div className={`flex gap-4 overflow-x-auto scrollbar-hide snap-x snap-mandatory px-2 py-2 ${className}`}>
      {agents.map((agent, index) => (
        <div
          key={agent.id}
          className="w-[200px] shrink-0 snap-center"
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
