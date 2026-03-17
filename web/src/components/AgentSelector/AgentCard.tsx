import { useRef, useEffect, useState, useCallback } from "react";
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

function PublicBadge({ selected }: { selected?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium px-3 py-1 rounded-full transition-colors duration-300"
      style={{
        background: selected ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.05)",
        color: selected ? "rgba(255,255,255,0.8)" : "rgba(0,0,0,0.45)",
      }}
    >
      <Globe size={11} />
      Public
    </span>
  );
}

function MicrosoftRequiredBadge({ selected }: { selected?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-2 text-[11px] font-semibold px-3 py-1 rounded-full whitespace-nowrap transition-colors duration-300"
      style={{
        background: selected ? "rgba(255,255,255,0.15)" : "rgba(0,103,184,0.08)",
        color: selected ? "rgba(255,255,255,0.9)" : "rgba(0,103,184,0.8)",
      }}
    >
      <Lock size={10} className="shrink-0" />
      <MicrosoftLogo size={12} />
      <span className="shrink-0">Microsoft account</span>
    </span>
  );
}

function OrgBadge({ selected }: { selected?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium px-3 py-1 rounded-full transition-colors duration-300"
      style={{
        background: selected ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.05)",
        color: selected ? "rgba(255,255,255,0.8)" : "rgba(0,0,0,0.45)",
      }}
    >
      <Lock size={10} />
      Organisation
    </span>
  );
}

function ComingSoonBadge() {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium px-3 py-1 rounded-full"
      style={{
        background: "rgba(0,0,0,0.04)",
        color: "rgba(0,0,0,0.35)",
      }}
    >
      <Clock size={11} />
      Coming soon
    </span>
  );
}

const BADGE_FOR_AUTH: Record<AuthLevel, React.ComponentType<{ selected?: boolean }>> = {
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
  const [tapping, setTapping] = useState(false);

  const handleClick = useCallback(() => {
    if (isDisabled) return;
    if (isLocked) {
      onSignInPrompt?.();
      return;
    }
    setTapping(true);
    setTimeout(() => setTapping(false), 300);
    onSelect(agent.id);
  }, [isDisabled, isLocked, onSignInPrompt, onSelect, agent.id]);

  useEffect(() => {
    if (selected && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    }
  }, [selected]);

  const accentColor = `var(${agent.accentVar})`;
  const isCoordinator = agent.id === "coordinator";
  const BadgeComponent = isDisabled
    ? ComingSoonBadge
    : isCoordinator
      ? null
      : BADGE_FOR_AUTH[agent.authLevel];

  return (
    <button
      ref={cardRef}
      type="button"
      onClick={handleClick}
      className={[
        "agent-card group relative rounded-2xl text-left w-full",
        "flex flex-col gap-3 p-5 transition-all duration-300 ease-out",
        "anim-fade-up-blur",
        tapping ? "agent-card-tap" : "",
        isDisabled
          ? "cursor-not-allowed"
          : isLocked
            ? "cursor-pointer hover:shadow-lg"
            : [
                "cursor-pointer",
                "hover:-translate-y-1 hover:shadow-xl",
                selected ? "agent-card-selected" : "",
              ].join(" "),
      ].join(" ")}
      style={{
        "--agent-accent": accentColor,
        animationDelay: `${baseDelay + index * 80}ms`,
        background: selected && !isDisabled && !isLocked ? accentColor : undefined,
      } as React.CSSProperties}
      aria-disabled={isDisabled || undefined}
      tabIndex={isDisabled ? -1 : 0}
    >
      {/* Disabled frosted overlay */}
      {isDisabled && (
        <div className="absolute inset-0 rounded-2xl bg-white/40 z-[1] pointer-events-none" />
      )}

      {/* Icon + title row */}
      <div className="flex items-center gap-3">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 transition-colors duration-300"
          style={{
            background: selected
              ? "rgba(255,255,255,0.2)"
              : `color-mix(in srgb, ${accentColor} 12%, transparent)`,
          }}
        >
          <AgentIcon
            iconName={agent.iconName}
            size={18}
            style={{
              color: isDisabled
                ? "rgba(0,0,0,0.25)"
                : selected
                  ? "#fff"
                  : accentColor,
              transition: "color 0.3s ease",
            }}
          />
        </div>

        <span
          className="font-display font-semibold text-lg tracking-tight leading-tight transition-colors duration-300"
          style={{
            color: isDisabled
              ? "rgba(0,0,0,0.3)"
              : selected
                ? "#fff"
                : "rgba(0,0,0,0.85)",
          }}
        >
          {agent.label}
        </span>

        {/* Lock indicator for locked agents */}
        {isLocked && (
          <Lock size={14} className="ml-auto shrink-0" style={{ color: "rgba(0,0,0,0.3)" }} />
        )}
      </div>

      {/* Description */}
      <p
        className="text-[13px] leading-relaxed transition-colors duration-300"
        style={{
          color: isDisabled
            ? "rgba(0,0,0,0.25)"
            : selected
              ? "rgba(255,255,255,0.8)"
              : "rgba(0,0,0,0.5)",
        }}
      >
        {agent.description}
      </p>

      {/* Badge */}
      {BadgeComponent && (
        <div className="mt-auto pt-1 relative z-[2]">
          <BadgeComponent selected={selected} />
        </div>
      )}

      {/* Selected checkmark */}
      {selected && !isDisabled && !isLocked && (
        <div
          className="absolute top-3 right-3 w-5 h-5 rounded-full flex items-center justify-center anim-scale-pop"
          style={{ background: "rgba(255,255,255,0.25)" }}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>
      )}
    </button>
  );
}
