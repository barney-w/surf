import type { DeveloperSettings } from "../hooks/useDeveloperSettings";
import { DEFAULTS } from "../hooks/useDeveloperSettings";

/* ------------------------------------------------------------------ */
/*  Types for debug & usage SSE event payloads                         */
/* ------------------------------------------------------------------ */

export interface SearchDebugInfo {
  original_query: string;
  rewritten_query: string | null;
  strategies_used: string[];
  results_per_strategy: Record<string, number>;
  total_results: number;
  tier_counts: Record<string, number>;
}

export interface UsageCall {
  model: string;
  input: number;
  output: number;
}

export interface UsageData {
  input_tokens: number;
  output_tokens: number;
  calls: UsageCall[];
}

export interface DebugData {
  search: SearchDebugInfo;
  quality_gate: string;
  agent: string;
}

export interface AnalysisData {
  debug: DebugData | null;
  usage: UsageData | null;
}

/* ------------------------------------------------------------------ */
/*  Style constants — muted dev-tools aesthetic                        */
/* ------------------------------------------------------------------ */

const panelStyle: React.CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: "12px",
  lineHeight: "1.5",
  border: "1px solid var(--color-border, #333)",
  borderRadius: "6px",
  background: "var(--color-surface, #1a1a2e)",
  color: "var(--color-text-secondary, #a0a0b0)",
  marginTop: "8px",
  overflow: "hidden",
};

const summaryStyle: React.CSSProperties = {
  cursor: "pointer",
  padding: "6px 12px",
  fontWeight: 600,
  fontSize: "11px",
  letterSpacing: "0.04em",
  textTransform: "uppercase" as const,
  userSelect: "none",
  color: "var(--color-text-muted, #888)",
};

const sectionStyle: React.CSSProperties = {
  padding: "8px 12px",
  borderTop: "1px solid var(--color-border, #333)",
};

const sectionTitleStyle: React.CSSProperties = {
  fontWeight: 600,
  fontSize: "10px",
  letterSpacing: "0.06em",
  textTransform: "uppercase" as const,
  color: "var(--color-text-muted, #888)",
  marginBottom: "4px",
};

const kvStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  padding: "1px 0",
};

const labelStyle: React.CSSProperties = {
  color: "var(--color-text-muted, #888)",
};

const valueStyle: React.CSSProperties = {
  color: "var(--color-text-primary, #e0e0e8)",
};

/* ------------------------------------------------------------------ */
/*  Quality gate badge                                                 */
/* ------------------------------------------------------------------ */

function QualityGateBadge({ outcome }: { outcome: string }) {
  const colours: Record<string, { bg: string; fg: string }> = {
    passed: { bg: "#16a34a22", fg: "#4ade80" },
    flagged: { bg: "#eab30822", fg: "#eab308" },
    failed: { bg: "#ef444422", fg: "#f87171" },
    not_run: { bg: "#71717a22", fg: "#a1a1aa" },
  };
  const c = colours[outcome] ?? colours.not_run;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: "9999px",
        fontSize: "11px",
        fontWeight: 600,
        background: c.bg,
        color: c.fg,
        border: `1px solid ${c.fg}44`,
      }}
    >
      {outcome}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface AnalysisPanelProps {
  data: AnalysisData;
  settings?: DeveloperSettings;
}

export function AnalysisPanel({ data, settings }: AnalysisPanelProps) {
  const { debug, usage } = data;

  // Only render when there is something to show
  if (!debug && !usage) return null;

  const search = debug?.search;
  const queryRewritten =
    search?.rewritten_query &&
    search.rewritten_query !== search.original_query;

  // Compute active overrides (settings that differ from defaults)
  const overrides: Array<{ key: string; value: string; defaultValue: string }> = [];
  if (settings) {
    const keyLabels: Record<keyof DeveloperSettings, string> = {
      topK: "Top K",
      strongThreshold: "Strong threshold",
      partialThreshold: "Partial threshold",
      enableVector: "Vector search",
      enableStitching: "Context stitching",
      enableBroadened: "Broadened search",
      enableKeyword: "Keyword search",
      enableRewrite: "Query rewrite",
      enableProofread: "Proofread",
    };
    for (const k of Object.keys(DEFAULTS) as Array<keyof DeveloperSettings>) {
      if (settings[k] !== DEFAULTS[k]) {
        overrides.push({
          key: keyLabels[k] ?? k,
          value: String(settings[k]),
          defaultValue: String(DEFAULTS[k]),
        });
      }
    }
  }

  return (
    <details style={panelStyle}>
      <summary style={summaryStyle}>Analysis</summary>

      {/* ── Search Details ── */}
      {search && (
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Search Details</div>
          <div style={kvStyle}>
            <span style={labelStyle}>Original query</span>
            <span style={valueStyle}>{search.original_query}</span>
          </div>
          {queryRewritten && (
            <div style={kvStyle}>
              <span style={labelStyle}>Rewritten query</span>
              <span style={valueStyle}>{search.rewritten_query}</span>
            </div>
          )}
          <div style={kvStyle}>
            <span style={labelStyle}>Strategies</span>
            <span style={valueStyle}>
              {search.strategies_used.length > 0
                ? search.strategies_used.join(", ")
                : "none"}
            </span>
          </div>
          {Object.keys(search.results_per_strategy).length > 0 && (
            <div style={{ marginTop: "2px" }}>
              <span style={labelStyle}>Results per strategy</span>
              <div style={{ paddingLeft: "12px" }}>
                {Object.entries(search.results_per_strategy).map(([strategy, count]) => (
                  <div key={strategy} style={kvStyle}>
                    <span style={labelStyle}>{strategy}</span>
                    <span style={valueStyle}>{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div style={kvStyle}>
            <span style={labelStyle}>Total results</span>
            <span style={valueStyle}>{search.total_results}</span>
          </div>
          {search.tier_counts && Object.keys(search.tier_counts).length > 0 && (
            <div style={{ marginTop: "2px" }}>
              <span style={labelStyle}>Tier breakdown</span>
              <div style={{ paddingLeft: "12px" }}>
                {Object.entries(search.tier_counts).map(([tier, count]) => (
                  <div key={tier} style={kvStyle}>
                    <span style={labelStyle}>{tier}</span>
                    <span style={valueStyle}>{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Quality Gate ── */}
      {debug && (
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Quality Gate</div>
          <QualityGateBadge outcome={debug.quality_gate} />
        </div>
      )}

      {/* ── Token Usage ── */}
      {usage && (
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Token Usage</div>
          <div style={kvStyle}>
            <span style={labelStyle}>Total input</span>
            <span style={valueStyle}>{usage.input_tokens.toLocaleString()}</span>
          </div>
          <div style={kvStyle}>
            <span style={labelStyle}>Total output</span>
            <span style={valueStyle}>{usage.output_tokens.toLocaleString()}</span>
          </div>
          {usage.calls.length > 0 && (
            <div style={{ marginTop: "4px" }}>
              <span style={labelStyle}>Per-call breakdown</span>
              <table
                style={{
                  width: "100%",
                  marginTop: "4px",
                  borderCollapse: "collapse",
                  fontSize: "11px",
                }}
              >
                <thead>
                  <tr style={{ color: "var(--color-text-muted, #888)" }}>
                    <th style={{ textAlign: "left", fontWeight: 600, paddingBottom: "2px" }}>
                      Model
                    </th>
                    <th style={{ textAlign: "right", fontWeight: 600, paddingBottom: "2px" }}>
                      Input
                    </th>
                    <th style={{ textAlign: "right", fontWeight: 600, paddingBottom: "2px" }}>
                      Output
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {usage.calls.map((call, i) => (
                    <tr key={i}>
                      <td style={{ color: "var(--color-text-primary, #e0e0e8)" }}>
                        {call.model}
                      </td>
                      <td style={{ textAlign: "right", color: "var(--color-text-primary, #e0e0e8)" }}>
                        {call.input.toLocaleString()}
                      </td>
                      <td style={{ textAlign: "right", color: "var(--color-text-primary, #e0e0e8)" }}>
                        {call.output.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Active Overrides ── */}
      {overrides.length > 0 && (
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Active Overrides</div>
          {overrides.map((o) => (
            <div key={o.key} style={kvStyle}>
              <span style={labelStyle}>{o.key}</span>
              <span style={valueStyle}>
                {o.value}{" "}
                <span style={{ ...labelStyle, fontSize: "10px" }}>
                  (default: {o.defaultValue})
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}
