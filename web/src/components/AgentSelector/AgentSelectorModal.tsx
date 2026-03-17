import {
  useState,
  useCallback,
  useEffect,
  useRef,
  useLayoutEffect,
} from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight } from "lucide-react";
import type { AgentWithAccess } from "./useAgentAccess";
import { AgentCard } from "./AgentCard";
import { AgentIcon } from "./AgentIcon";

type AgentSelectorModalProps = {
  agents: AgentWithAccess[];
  selectedAgent: AgentWithAccess;
  onSelect: (id: string) => void;
  onSignInPrompt?: () => void;
};

type ModalState = "closed" | "opening" | "open" | "closing";

/* ── Animated agent label that morphs when the selection changes ── */
function AnimatedAgentLabel({
  agent,
  accentColor,
}: {
  agent: AgentWithAccess;
  accentColor: string;
}) {
  const [displayed, setDisplayed] = useState(agent);
  const [animating, setAnimating] = useState(false);
  const prevId = useRef(agent.id);

  useEffect(() => {
    if (agent.id === prevId.current) return;
    prevId.current = agent.id;
    setAnimating(true);
    const t = setTimeout(() => {
      setDisplayed(agent);
      setAnimating(false);
    }, 200);
    return () => clearTimeout(t);
  }, [agent]);

  return (
    <span
      className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-display font-semibold text-text-primary border glass-panel whitespace-nowrap transition-all duration-300"
      style={{
        boxShadow: `0 0 12px color-mix(in srgb, ${accentColor} 25%, transparent)`,
        borderColor: `color-mix(in srgb, ${accentColor} 40%, var(--surf-color-border-default))`,
      }}
    >
      <span
        className="w-2 h-2 rounded-full transition-colors duration-300"
        style={{ background: accentColor }}
      />
      <span
        className="transition-all duration-200 inline-block"
        style={{
          opacity: animating ? 0 : 1,
          transform: animating ? "translateY(6px)" : "translateY(0)",
          filter: animating ? "blur(2px)" : "blur(0)",
        }}
      >
        {displayed.label}
      </span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-text-muted"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </span>
  );
}

/* ── Swipeable carousel with scroll indicators ── */
function AgentCarousel({
  agents,
  selectedId,
  onSelect,
  onSignInPrompt,
}: {
  agents: AgentWithAccess[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSignInPrompt?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const checkScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 4);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 4);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    checkScroll();
    el.addEventListener("scroll", checkScroll, { passive: true });
    const ro = new ResizeObserver(checkScroll);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", checkScroll);
      ro.disconnect();
    };
  }, [checkScroll]);

  const scroll = useCallback((dir: "left" | "right") => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir === "left" ? -280 : 280, behavior: "smooth" });
  }, []);

  return (
    <div className="relative">
      {/* Scroll arrow — left */}
      {canScrollLeft && (
        <button
          type="button"
          onClick={() => scroll("left")}
          className="absolute left-1 top-1/2 -translate-y-1/2 z-10 w-9 h-9 rounded-full glass-panel border border-border flex items-center justify-center
                     hover:border-border-strong hover:shadow-md transition-all cursor-pointer"
          aria-label="Scroll left"
        >
          <ChevronLeft size={18} className="text-text-secondary" />
        </button>
      )}

      {/* Scroll arrow — right */}
      {canScrollRight && (
        <button
          type="button"
          onClick={() => scroll("right")}
          className="absolute right-1 top-1/2 -translate-y-1/2 z-10 w-9 h-9 rounded-full glass-panel border border-border flex items-center justify-center
                     hover:border-border-strong hover:shadow-md transition-all cursor-pointer"
          aria-label="Scroll right"
        >
          <ChevronRight size={18} className="text-text-secondary" />
        </button>
      )}

      {/* Scrollable row */}
      <div
        ref={scrollRef}
        className="flex gap-5 overflow-x-auto overflow-y-visible px-6 py-6 pb-8 snap-x snap-mandatory scrollbar-hide"
      >
        {agents.map((agent, index) => (
          <div key={agent.id} className="w-[250px] shrink-0 snap-center flex">
            <AgentCard
              agent={agent}
              selected={selectedId === agent.id}
              onSelect={onSelect}
              onSignInPrompt={onSignInPrompt}
              index={index}
              baseDelay={150}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export function AgentSelectorModal({
  agents,
  selectedAgent,
  onSelect,
  onSignInPrompt,
}: AgentSelectorModalProps) {
  const [modalState, setModalState] = useState<ModalState>("closed");
  const closingRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const open = useCallback(() => {
    setModalState("opening");
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setModalState("open"));
    });
  }, []);

  const close = useCallback(() => {
    setModalState("closing");
    closingRef.current = setTimeout(() => setModalState("closed"), 300);
  }, []);

  useEffect(() => () => clearTimeout(closingRef.current), []);

  const handleSelect = useCallback(
    (id: string) => {
      onSelect(id);
      close();
    },
    [onSelect, close],
  );

  const handleBackdrop = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) close();
    },
    [close],
  );

  useEffect(() => {
    if (modalState === "closed") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [modalState, close]);

  useLayoutEffect(() => {
    if (modalState === "closed") return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [modalState]);

  const isVisible = modalState !== "closed";
  const isIn = modalState === "open";
  const accentColor = `var(${selectedAgent.accentVar})`;

  return (
    <>
      {/* ── Trigger: logo + animated agent label + "tap to change" hint ── */}
      <div className="flex flex-col items-center gap-1">
        <button
          type="button"
          onClick={open}
          className="group relative cursor-pointer transition-transform duration-200 hover:scale-[1.02] active:scale-[0.97]"
        >
          <div
            className="w-28 h-28 sm:w-32 sm:h-32 rounded-2xl overflow-hidden transition-all duration-500"
            style={{
              boxShadow: `0 0 0 2.5px ${accentColor}, 0 0 32px color-mix(in srgb, ${accentColor} 20%, transparent)`,
            }}
          >
            <img
              src="/surf.png"
              alt="Surf"
              className="w-full h-full object-cover"
            />
          </div>

          {/* Agent label pill */}
          <div className="absolute -bottom-4 left-1/2 -translate-x-1/2">
            <AnimatedAgentLabel
              agent={selectedAgent}
              accentColor={accentColor}
            />
          </div>
        </button>

        {/* "Tap to change" hint below the pill */}
        <button
          type="button"
          onClick={open}
          className="mt-5 text-[11px] text-text-muted hover:text-text-secondary transition-colors cursor-pointer"
        >
          Tap to change agent
        </button>
      </div>

      {/* ── Modal via portal ── */}
      {isVisible &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Choose your agent"
            className={`fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 transition-all ${
              isIn
                ? "agent-modal-backdrop-in"
                : "agent-modal-backdrop-out"
            }`}
            style={{ transitionDuration: "400ms" }}
            onClick={handleBackdrop}
          >
            <div
              className={`agent-modal-panel relative rounded-3xl border border-white/[0.08] w-full transition-all flex flex-col overflow-hidden
                          max-h-[90vh] sm:max-h-[85vh]
                          max-w-[95vw] sm:max-w-[900px] ${
                            isIn
                              ? "opacity-100 scale-100 translate-y-0"
                              : "opacity-0 scale-[0.92] translate-y-8"
                          }`}
              style={{
                transitionDuration: "350ms",
                transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
              }}
            >

              {/* Close button */}
              <button
                type="button"
                onClick={close}
                className="absolute top-4 right-4 z-10 w-8 h-8 rounded-full flex items-center justify-center
                           text-white/50 hover:text-white hover:bg-white/10
                           transition-all duration-150 cursor-pointer"
                aria-label="Close"
              >
                <X size={16} />
              </button>

              {/* Header */}
              <div className="relative z-[1] flex flex-col items-center pt-8 pb-4 px-6 sm:pt-10 sm:pb-6">
                <div className="relative mb-4">
                  <img
                    src="/surf.png"
                    alt=""
                    className="w-16 h-16 sm:w-20 sm:h-20 rounded-2xl"
                    style={{ boxShadow: "0 8px 32px rgba(0,0,0,0.3)" }}
                  />
                </div>
                <h2 className="font-display text-xl sm:text-2xl font-semibold text-white/95">
                  Choose your agent
                </h2>
                <p className="text-sm text-white/50 mt-2 text-center max-w-md">
                  Select a specialist or let the coordinator route your question
                  automatically.
                </p>
              </div>

              {/* Agent carousel */}
              <div className="relative z-[1] pb-8 sm:pb-10">
                <AgentCarousel
                  agents={agents}
                  selectedId={selectedAgent.id}
                  onSelect={handleSelect}
                  onSignInPrompt={onSignInPrompt}
                />
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
