/**
 * LatencyChart — vizualizacija razlaganja latencije po fazama.
 *
 * Ovo je važan element za diplomski rad: pokazuje kako se total_ms
 * dijeli na LLM, validation i execution. Faza 4 benchmark koristit će
 * iste brojeve kroz BenchmarkRunner.
 *
 * Implementacija: plain SVG bar chart, bez chart biblioteke. Razlog:
 * 4 stupca, statičke ose — recharts (~50KB) bi bio overkill.
 */

import type { LatencyBreakdown } from "@/lib/types";

interface BarSegment {
  label: string;
  value: number;
  color: string;
}

export function LatencyChart({ latency }: { latency: LatencyBreakdown }) {
  // Nule kao default za null vrijednosti — chart se može renderati prije
  // nego svi koraci popune (npr. samo LLM ako execution nije pokrenuto).
  const llm = latency.llm_ms ?? 0;
  const validation = latency.validation_ms ?? 0;
  const execution = latency.execution_ms ?? 0;
  const total = latency.total_ms ?? llm + validation + execution;

  const segments: BarSegment[] = [
    { label: "LLM", value: llm, color: "#6366f1" },             // indigo-500
    { label: "Validacija", value: validation, color: "#10b981" }, // emerald-500
    { label: "Izvršavanje", value: execution, color: "#f59e0b" },  // amber-500
  ];

  // Skala — najveći segment (ili total) zauzima cijelu širinu trake.
  // Koristimo total da relativni odnos LLM vs validation bude vidljiv.
  const scale = Math.max(total, 1); // izbjegni dijeljenje s 0

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Latency breakdown
        </h3>
        <span className="text-xs font-mono text-zinc-500">total {formatMs(total)}</span>
      </div>

      <div className="space-y-2">
        {segments.map((seg) => (
          <div key={seg.label}>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-zinc-600 dark:text-zinc-400">{seg.label}</span>
              <span className="font-mono text-zinc-700 dark:text-zinc-300">
                {formatMs(seg.value)}
              </span>
            </div>
            <div className="h-3 rounded bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
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
