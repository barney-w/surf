import { useRef, useEffect } from "react";
import { Lock, Globe, Clock } from "lucide-react";
import type { AgentWithAccess } from "./useAgentAccess";
import type { AuthLevel } from "./agentConfig";
import { AgentIcon } from "./AgentIcon";

type AgentCardProps = {
  agent: AgentWithAccess;
  selected: boolean;
  onSelect: (id: string) => void;
  onSignInPrompt?: () => void;
  index: number;
  baseDelay?: number;
};

function MicrosoftLogo({ size = 14 }: { size?: number }) {
  const half = size / 2;
  const gap = size * 0.06;
  const block = half - gap;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} fill="none">
      <rect x="0" y="0" width={block} height={block} fill="#F25022" />
      <rect x={half + gap} y="0" width={block} height={block} fill="#7FBA00" />
      <rect x="0" y={half + gap} width={block} height={block} fill="#00A4EF" />
      <rect x={half + gap} y={half + gap} width={block} height={block} fill="#FFB900" />
    </svg>
  );
}

/* ── Badge components ── */

function PublicBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-3 py-1.5 rounded-xl border border-border/60 text-text-secondary bg-surface-raised/50">
      <Globe size={12} className="text-text-muted" />
      Public
    </span>
  );
}

function MicrosoftRequiredBadge() {
  return (
    <span
      className="inline-flex items-center gap-2 text-[11px] font-semibold px-3 py-1.5 rounded-xl whitespace-nowrap
                 bg-[#0078D4] text-white shadow-sm
                 group-hover:bg-[#106EBE] group-hover:shadow-md
                 transition-all duration-200"
    >
      <Lock size={11} className="shrink-0" />
      <MicrosoftLogo size={12} />
      <span className="shrink-0">Microsoft account</span>
    </span>
  );
}

function OrgBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-3 py-1.5 rounded-xl border border-border/60 text-text-secondary bg-surface-raised/50">
      <Lock size={11} className="text-text-muted" />
      Organisation
    </span>
  );
}

function ComingSoonBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-3.5 py-1.5 rounded-xl border border-border/40 text-text-muted bg-surface-sunken/60 backdrop-blur-sm">
      <Clock size={12} />
      Coming soon
    </span>
  );
}

const BADGE_FOR_AUTH: Record<AuthLevel, React.ComponentType> = {
  public: PublicBadge,
  microsoft: MicrosoftRequiredBadge,
  organisational: OrgBadge,
};

export function AgentCard({
  agent,
  selected,
  onSelect,
  onSignInPrompt,
  index,
  baseDelay = 0,
}: AgentCardProps) {
  const isLocked = !agent.accessible && agent.enabled;
  const isDisabled = !agent.enabled;
  const cardRef = useRef<HTMLButtonElement>(null);

  const handleClick = () => {
    if (isDisabled) return;
    if (isLocked) {
      onSignInPrompt?.();
      return;
    }
    onSelect(agent.id);
  };

  useEffect(() => {
    if (selected && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    }
  }, [selected]);

  const accentColor = `var(${agent.accentVar})`;
  const BadgeComponent = isDisabled ? ComingSoonBadge : BADGE_FOR_AUTH[agent.authLevel];

  return (
    <button
      ref={cardRef}
      type="button"
      onClick={handleClick}
      className={[
        "agent-card group relative rounded-2xl border text-left w-full h-full",
        "flex flex-col items-center gap-2.5 p-5 transition-all duration-300 ease-out",
        "anim-fade-up",
        isDisabled
          ? "cursor-not-allowed border-border"
          : isLocked
            ? "cursor-pointer border-border hover:border-border-strong"
            : [
                "cursor-pointer border-border",
                "hover:-translate-y-1 hover:shadow-xl",
                selected
                  ? "border-transparent"
                  : "hover:border-border-strong",
              ].join(" "),
      ].join(" ")}
      style={{
        animationDelay: `${baseDelay + index * 60}ms`,
        ...(selected && !isDisabled && !isLocked
          ? {
              background: `linear-gradient(160deg, color-mix(in srgb, ${accentColor} 10%, var(--surf-color-bg-surface-raised)), var(--surf-color-bg-surface-raised))`,
              borderColor: accentColor,
              boxShadow: `0 0 0 1px color-mix(in srgb, ${accentColor} 30%, transparent), 0 8px 32px color-mix(in srgb, ${accentColor} 15%, transparent)`,
            }
          : {}),
      }}
      aria-disabled={isDisabled || undefined}
      tabIndex={isDisabled ? -1 : 0}
    >
      {/* Top accent line */}
      <div
        className="absolute top-0 left-0 right-0 h-[2px] transition-all duration-500"
        style={{
          background: `linear-gradient(90deg, transparent 10%, ${accentColor}, transparent 90%)`,
          opacity: selected ? 1 : 0,
          transform: selected ? "scaleX(1)" : "scaleX(0.3)",
        }}
      />

      {/* Disabled overlay — soft frosted layer */}
      {isDisabled && (
        <div className="absolute inset-0 rounded-2xl bg-canvas/40 z-[1] pointer-events-none" />
      )}

      {/* Agent icon */}
      <div className="relative mt-1">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center transition-all duration-300"
          style={{
            background: isDisabled
              ? "color-mix(in srgb, var(--surf-color-text-muted) 8%, transparent)"
              : `color-mix(in srgb, ${accentColor} 12%, transparent)`,
            boxShadow: selected
              ? `0 0 20px color-mix(in srgb, ${accentColor} 25%, transparent)`
              : undefined,
          }}
        >
          <AgentIcon
            iconName={agent.iconName}
            size={28}
            style={{
              color: isDisabled
                ? "var(--surf-color-text-muted)"
                : isLocked
                  ? "var(--surf-color-text-muted)"
                  : accentColor,
            }}
          />
        </div>
        {isLocked && (
          <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full bg-surface-raised border border-border flex items-center justify-center shadow-sm">
            <Lock size={10} className="text-text-muted" />
          </div>
        )}
      </div>

      {/* Agent name */}
      <span className={`font-display font-semibold text-sm text-center leading-tight ${isDisabled ? "text-text-muted" : "text-text-primary"}`}>
        {agent.label}
      </span>

      {/* Description — hidden via overflow, fixed height, no clamp truncation */}
      <p className={`text-[11px] text-center leading-relaxed h-[3em] overflow-hidden ${isDisabled ? "text-text-muted/70" : "text-text-secondary"}`}>
        {agent.description}
      </p>

      {/* Badge — always at bottom, in front of disabled overlay */}
      <div className="mt-auto pt-1.5 relative z-[2]">
        <BadgeComponent />
      </div>

      {/* Selected checkmark */}
      {selected && !isDisabled && !isLocked && (
        <div
          className="absolute top-2.5 right-2.5 w-5 h-5 rounded-full flex items-center justify-center anim-scale-pop"
          style={{ background: accentColor }}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>
      )}
    </button>
  );
}
