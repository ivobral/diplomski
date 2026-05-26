/**
 * QueryHistory — prikaz zadnjih 10 pitanja iz localStorage-a.
 *
 * Klik na unos vraća pitanje, provider i strategiju u QueryForm
 * preko `onPick` callbacka. Praktično za demo gdje cikliraš set
 * pripremljenih pitanja, i za testiranje eksperimenata (isto pitanje,
 * različite strategije/provideri).
 */
"use client";

import type { HistoryEntry } from "@/lib/history";

interface Props {
  entries: HistoryEntry[];
  onPick: (entry: HistoryEntry) => void;
  onClear: () => void;
}

export function QueryHistory({ entries, onPick, onClear }: Props) {
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900 p-4 text-sm text-zinc-500 italic">
        Povijest pitanja prazna. Nakon prvog upita, prikazat će se ovdje.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Povijest ({entries.length})
        </h3>
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-zinc-500 hover:text-rose-600 dark:hover:text-rose-400 transition-colors"
        >
          Očisti
        </button>
      </div>

      <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
        {entries.map((entry, idx) => (
          <li key={idx}>
            <button
              type="button"
              onClick={() => onPick(entry)}
              className="w-full text-left px-4 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors"
            >
              <div className="text-sm text-zinc-800 dark:text-zinc-200 truncate">
                {entry.question}
              </div>
              <div className="text-xs text-zinc-500 mt-0.5 flex gap-2 items-center">
                {entry.provider && <span className="font-mono">{entry.provider}</span>}
                {entry.strategy && (
                  <span className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 font-mono">
                    {entry.strategy}
                  </span>
                )}
                <span className="text-zinc-400">{formatRelative(entry.timestamp)}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatRelative(iso: string): string {
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "upravo";
  if (diffMin < 60) return `prije ${diffMin} min`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `prije ${diffH} h`;
  return date.toLocaleDateString("hr-HR");
}
