/**
 * LatencyChart — per-phase latency breakdown of the pipeline.
 *
 * Shows how long each step took (LLM call, validation, execution) — useful
 * for the thesis because it demonstrates that the LLM dominates total time,
 * not the validator or the database. Plain SVG bar chart, no chart library
 * (overkill for 3 segments).
 */

import type { LatencyBreakdown } from "@/lib/types";

interface BarSegment {
  label: string;
  value: number;
  color: string;
  hint: string;
}

export function LatencyChart({ latency }: { latency: LatencyBreakdown }) {
  const promptBuild = latency.prompt_build_ms ?? 0;
  const llm = latency.llm_ms ?? 0;
  const validation = latency.validation_ms ?? 0;
  const execution = latency.execution_ms ?? 0;
  const total =
    latency.total_ms ?? promptBuild + llm + validation + execution;

  // Warm palette — each phase a distinct hue for at-a-glance signal.
  const segments: BarSegment[] = [
    {
      label: "Prompt build",
      value: promptBuild,
      color: "#a16207",
      hint: "Fetch schema, format tables/relations, assemble prompt",
    },
    {
      label: "LLM",
      value: llm,
      color: "#d97706",
      hint: "Generate SQL (LLM API call, cumulative over retries)",
    },
    {
      label: "Validation",
      value: validation,
      color: "#15803d",
      hint: "AST parsing + safety + semantic checks",
    },
    {
      label: "Execution",
      value: execution,
      color: "#0369a1",
      hint: "Run SQL against the database (read-only)",
    },
  ];

  const scale = Math.max(total, 1); // avoid division by zero

  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-stone-900">
          Latency by phase
        </h3>
        <span className="text-xs font-mono text-stone-500">
          total {formatMs(total)}
        </span>
      </div>

      <div className="space-y-2.5">
        {segments.map((seg) => (
          <div key={seg.label} title={seg.hint}>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-stone-700">{seg.label}</span>
              <span className="font-mono text-stone-600">{formatMs(seg.value)}</span>
            </div>
            <div className="h-2.5 rounded bg-stone-100 overflow-hidden">
              <div
                className="h-full rounded transition-all"
                style={{
                  width: `${(seg.value / scale) * 100}%`,
                  backgroundColor: seg.color,
                  minWidth: seg.value > 0 ? "2px" : "0",
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatMs(ms: number): string {
  if (ms < 1) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
