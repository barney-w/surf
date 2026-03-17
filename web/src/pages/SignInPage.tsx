import { WaveLoader } from "@surf-kit/core";
import { useAuth } from "../auth/AuthProvider";
import { BackgroundSlideshow } from "../components/BackgroundSlideshow";
import { ThemeToggle } from "../components/ThemeToggle";

function MicrosoftLogo({ size = 16 }: { size?: number }) {
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

function UserIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-text-secondary"
    >
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-text-muted group-hover:text-accent transition-colors"
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

export function SignInPage() {
  const { login, loginAsGuest, isGuestLoading, error } = useAuth();

  return (
    <div className="flex flex-col h-full bg-canvas relative overflow-hidden">
      <BackgroundSlideshow />

      {/* Theme toggle — top right */}
      <div className="absolute top-4 right-4 z-10">
        <ThemeToggle />
      </div>

      {/* Centred content */}
      <div className="flex-1 flex flex-col items-center justify-center relative z-[1] px-4">
        <div className="anim-fade-up flex flex-col items-center w-full max-w-sm">
          {/* Logo */}
          <img
            src="/surf.png"
            alt="Surf"
            className="w-24 h-24 rounded-xl shadow-lg mb-6"
          />

          {/* Title & subtitle */}
          <h1 className="font-display text-2xl font-semibold text-text-primary tracking-tight">
            Surf
          </h1>
          <p className="text-text-secondary text-sm mt-1.5 mb-8 text-center max-w-xs">
            I can coordinate specialist agents to answer your questions.
          </p>

          {/* Sign-in options */}
          <div className="w-full flex flex-col gap-3">
            {/* Microsoft Entra */}
            <button
              onClick={login}
              className="group w-full glass-panel px-5 py-4 flex items-center gap-4 rounded-xl
                         border border-border-strong hover:border-accent
                         transition-all duration-200 cursor-pointer
                         hover:shadow-md active:scale-[0.98]"
            >
              <div className="w-10 h-10 rounded-lg bg-surface-raised flex items-center justify-center shrink-0">
                <MicrosoftLogo size={20} />
              </div>
              <div className="flex-1 text-left">
                <p className="font-display font-semibold text-sm text-text-primary">
                  Microsoft Entra
                </p>
                <p className="text-xs text-text-secondary mt-0.5">
                  Organisation or personal account
                </p>
              </div>
              <ChevronRight />
            </button>

            {/* Guest / limited */}
            <button
              onClick={loginAsGuest}
              disabled={isGuestLoading}
              className={`group w-full glass-panel px-5 py-4 flex items-center gap-4 rounded-xl
                         border border-border hover:border-border-strong
                         transition-all duration-200
                         hover:shadow-sm active:scale-[0.98]
                         ${isGuestLoading ? "opacity-70 cursor-wait" : "cursor-pointer"}`}
            >
              <div className="w-10 h-10 rounded-lg bg-surface-raised flex items-center justify-center shrink-0">
                {isGuestLoading ? <WaveLoader size="sm" color="#38bdf8" /> : <UserIcon />}
              </div>
              <div className="flex-1 text-left">
                <p className="font-display font-semibold text-sm text-text-primary">
                  Continue without account
                </p>
                <p className="text-xs text-text-secondary mt-0.5">
                  Limited functionality
                </p>
              </div>
              {!isGuestLoading && <ChevronRight />}
            </button>
          </div>

          {/* Error message */}
          {error && (
            <p className="text-sm text-red-400 text-center mt-4 anim-fade-up">
              {error}
            </p>
          )}
        </div>
      </div>

    </div>
  );
}
