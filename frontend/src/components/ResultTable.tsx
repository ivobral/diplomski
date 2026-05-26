/**
 * ResultTable — tablični prikaz redaka iz QueryResponse.
 *
 * Implementacija je namjerno najjednostavnija (HTML table + Tailwind):
 * - auto-LIMIT u backendu garantira <= DEFAULT_LIMIT redaka (1000),
 *   pa virtualizacija (TanStack) ne treba,
 * - sticky header za scroll u dugim tablicama.
 */

import type { QueryResponse } from "@/lib/types";

export function ResultTable({ response }: { response: QueryResponse }) {
  if (!response.executed) return null;

  if (response.rows.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-4 text-sm text-zinc-500 italic">
        Upit je izvršen, ali nema redaka u rezultatu.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
      <div className="max-h-[480px] overflow-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-zinc-50 dark:bg-zinc-900 sticky top-0 z-10">
            <tr>
              {response.columns.map((col) => (
                <th
                  key={col}
                  className="text-left px-4 py-2 font-semibold text-zinc-700 dark:text-zinc-300 border-b border-zinc-200 dark:border-zinc-800"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {response.rows.map((row, rowIdx) => (
              <tr
                key={rowIdx}
                className="border-b border-zinc-100 dark:border-zinc-800 last:border-0"
              >
                {row.map((cell, cellIdx) => (
                  <td
                    key={cellIdx}
                    className="px-4 py-2 text-zinc-800 dark:text-zinc-200 font-mono whitespace-nowrap"
                  >
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-2 text-xs text-zinc-500 bg-zinc-50 dark:bg-zinc-900 border-t border-zinc-200 dark:border-zinc-800">
        {response.row_count} {response.row_count === 1 ? "redak" : "redaka"}
      </div>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}
