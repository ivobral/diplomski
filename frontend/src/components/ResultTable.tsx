/**
 * ResultTable — tabular view of rows returned by the query.
 *
 * Kept intentionally simple (HTML table + Tailwind):
 *  - the backend auto-LIMIT keeps row count <= DEFAULT_LIMIT (1000),
 *    so virtualization is not needed,
 *  - sticky header for scrolling through longer result sets.
 */

import type { QueryResponse } from "@/lib/types";

export function ResultTable({ response }: { response: QueryResponse }) {
  if (!response.executed) return null;

  if (response.rows.length === 0) {
    return (
      <div className="rounded-lg border border-stone-200 bg-white p-4 text-sm text-stone-500 italic">
        The query executed but returned no rows.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-stone-200 bg-white overflow-hidden">
      <div className="max-h-[480px] overflow-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-amber-50 sticky top-0 z-10">
            <tr>
              {response.columns.map((col) => (
                <th
                  key={col}
                  className="text-left px-4 py-2 font-semibold text-stone-800 border-b border-amber-100"
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
                className="border-b border-stone-100 last:border-0 hover:bg-stone-50/50"
              >
                {row.map((cell, cellIdx) => (
                  <td
                    key={cellIdx}
                    className="px-4 py-2 text-stone-800 font-mono whitespace-nowrap"
                  >
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-2 text-xs text-stone-500 bg-stone-50 border-t border-stone-200">
        {response.row_count} {response.row_count === 1 ? "row" : "rows"}
      </div>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}
