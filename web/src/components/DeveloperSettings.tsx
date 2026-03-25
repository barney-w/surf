import { Dialog, Button } from "@surf-kit/core";
import type { DeveloperSettings as Settings } from "../hooks/useDeveloperSettings";
import { DEFAULTS } from "../hooks/useDeveloperSettings";

/* ------------------------------------------------------------------ */
/*  Reusable form controls                                             */
/* ------------------------------------------------------------------ */

function NumberField({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
}) {
  return (
    <label className="flex items-center justify-between gap-4">
      <span className="text-sm text-text-primary">{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-sm text-text-primary
                   focus:outline-none focus:ring-1 focus:ring-accent"
      />
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-4 cursor-pointer">
      <span className="text-sm text-text-primary">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-border text-accent focus:ring-accent cursor-pointer"
      />
    </label>
  );
}

/* ------------------------------------------------------------------ */
/*  Settings dialog                                                    */
/* ------------------------------------------------------------------ */

interface DeveloperSettingsProps {
  isOpen: boolean;
  onClose: () => void;
  settings: Settings;
  onUpdate: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
  onReset: () => void;
}

export function DeveloperSettings({
  isOpen,
  onClose,
  settings,
  onUpdate,
  onReset,
}: DeveloperSettingsProps) {
  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title="Developer Settings"
      size="sm"
      footer={
        <div className="flex w-full items-center justify-between">
          <Button intent="secondary" size="sm" onPress={onReset}>
            Reset to Defaults
          </Button>
          <Button intent="primary" size="sm" onPress={onClose}>
            Done
          </Button>
        </div>
      }
    >
      <div className="space-y-5">
        {/* Search parameters */}
        <fieldset>
          <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
            Search Parameters
          </legend>
          <div className="space-y-3">
            <NumberField
              label="Top K"
              value={settings.topK}
              onChange={(v) => onUpdate("topK", v)}
              min={1}
              max={50}
            />
            <NumberField
              label="Strong threshold"
              value={settings.strongThreshold}
              onChange={(v) => onUpdate("strongThreshold", v)}
              step={0.05}
              min={0}
              max={1}
            />
            <NumberField
              label="Partial threshold"
              value={settings.partialThreshold}
              onChange={(v) => onUpdate("partialThreshold", v)}
              step={0.05}
              min={0}
              max={1}
            />
          </div>
        </fieldset>

        {/* Strategy toggles */}
        <fieldset>
          <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
            RAG Strategies
          </legend>
          <div className="space-y-3">
            <Toggle
              label="Vector search"
              checked={settings.enableVector}
              onChange={(v) => onUpdate("enableVector", v)}
            />
            <Toggle
              label="Context stitching"
              checked={settings.enableStitching}
              onChange={(v) => onUpdate("enableStitching", v)}
            />
            <Toggle
              label="Broadened search (Strategy 2)"
              checked={settings.enableBroadened}
              onChange={(v) => onUpdate("enableBroadened", v)}
            />
            <Toggle
              label="Keyword search (Strategy 3)"
              checked={settings.enableKeyword}
              onChange={(v) => onUpdate("enableKeyword", v)}
            />
          </div>
        </fieldset>

        {/* Processing toggles */}
        <fieldset>
          <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
            Processing
          </legend>
          <div className="space-y-3">
            <Toggle
              label="Query rewrite (Strategy 0)"
              checked={settings.enableRewrite}
              onChange={(v) => onUpdate("enableRewrite", v)}
            />
            <Toggle
              label="Proofread responses"
              checked={settings.enableProofread}
              onChange={(v) => onUpdate("enableProofread", v)}
            />
          </div>
        </fieldset>

        {/* Current defaults reference */}
        <p className="text-xs text-text-muted">
          Defaults: topK={DEFAULTS.topK}, strong={DEFAULTS.strongThreshold},
          partial={DEFAULTS.partialThreshold}
        </p>
      </div>
    </Dialog>
  );
}
