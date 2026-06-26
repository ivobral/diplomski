/**
 * QueryHistory — last 10 questions from localStorage.
 *
 * Clicking an entry restores the question + strategy into the QueryForm
 * via the ``onPick`` callback. Useful for demos (cycling through a set
 * of prepared questions) and for testing different strategies against
 * the same question.
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
      <div className="rounded-lg border border-stone-200 bg-white p-4 text-sm text-stone-500 italic">
        No history yet. Your questions will appear here after the first query.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-stone-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-amber-50 border-b border-amber-100">
        <h3 className="text-sm font-semibold text-stone-900">
          History ({entries.length})
        </h3>
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-stone-600 hover:text-rose-700 transition-colors"
        >
          Clear
        </button>
      </div>

      <ul className="divide-y divide-stone-100">
        {entries.map((entry, idx) => (
          <li key={idx}>
            <button
              type="button"
              onClick={() => onPick(entry)}
              className="w-full text-left px-4 py-2.5 hover:bg-stone-50 transition-colors"
              title="Click to load this question back into the form"
            >
              <div className="text-sm text-stone-800 truncate">{entry.question}</div>
              <div className="text-xs text-stone-500 mt-0.5">
                {formatRelative(entry.timestamp)}
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
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} h ago`;
  return date.toLocaleDateString("en-US");
}
