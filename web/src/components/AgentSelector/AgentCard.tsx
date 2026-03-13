import type { AgentWithAccess } from "./useAgentAccess";
import type { AuthLevel } from "./agentConfig";
import { Shield } from "@surf-kit/icons";
import { ICON_MAP } from "./iconMap";

type AgentCardProps = {
  agent: AgentWithAccess;
  selected: boolean;
  onSelect: (id: string) => void;
  onSignInPrompt?: () => void;
  index: number;
};

const AUTH_LABELS: Record<AuthLevel, string> = {
  public: "Public",
  microsoft: "Microsoft Account",
  organisational: "Organisation",
};

export function AgentCard({
  agent,
  selected,
  onSelect,
  onSignInPrompt,
  index,
}: AgentCardProps) {
  const Icon = ICON_MAP[agent.iconName];
  const isLocked = !agent.accessible && agent.enabled;
  const isDisabled = !agent.enabled;

  const handleClick = () => {
    if (isDisabled) return;
    if (isLocked) {
      onSignInPrompt?.();
      return;
    }
    onSelect(agent.id);
  };

  const stateClasses = isDisabled
    ? "opacity-40 cursor-not-allowed pointer-events-none"
    : isLocked
      ? "opacity-60 cursor-pointer"
      : [
          "hover:border-accent hover:shadow-md active:scale-[0.98] cursor-pointer",
          selected ? "border-accent ring-1 ring-accent/20" : "",
        ].join(" ");

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`glass-panel px-4 py-3 rounded-xl border border-border transition-all duration-200 text-left w-full anim-fade-up ${stateClasses}`}
      style={{
        animationDelay: `${index * 60}ms`,
        ...(selected && !isDisabled && !isLocked
          ? { boxShadow: "var(--shadow-glow-cyan)" }
          : {}),
      }}
      aria-disabled={isDisabled || undefined}
      tabIndex={isDisabled ? -1 : 0}
    >
      <div className="flex flex-col gap-2">
        {/* Header row */}
        <div className="flex items-center gap-2">
          {Icon && (
            <span style={{ color: `var(${agent.accentVar})` }}>
              <Icon size={20} />
            </span>
          )}
          <span className="font-display font-semibold text-sm text-text-primary">
            {agent.label}
          </span>
          {isLocked && (
            <Shield size={14} className="text-text-muted ml-auto" />
          )}
        </div>

        {/* Description */}
        <p className="text-xs text-text-secondary line-clamp-2">
          {agent.description}
        </p>

        {/* Badge row */}
        <div>
          {isDisabled ? (
            <span className="text-[10px] text-text-muted">Coming soon</span>
          ) : (
            <span className="text-[10px] px-2 py-0.5 rounded-full border border-border text-text-muted">
              {AUTH_LABELS[agent.authLevel]}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}
