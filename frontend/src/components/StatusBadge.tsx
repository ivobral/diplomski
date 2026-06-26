/**
 * StatusBadge — at-a-glance indicator of the query response state.
 *
 * Three primary states (mutually exclusive per backend logic):
 *  - executed: validation + execution succeeded → emerald (green)
 *  - blocked:  safety block (DDL/DML/multi-statement) → rose (red)
 *  - error:    invalid SQL from parse/semantic (retry exhausted) → amber
 */

import type { QueryResponse } from "@/lib/types";

export function StatusBadge({ response }: { response: QueryResponse }) {
  if (response.blocked_reason) {
    return <Badge color="rose">Blocked · security layer</Badge>;
  }
  if (response.error) {
    return (
      <Badge color="amber">
        Error · {response.error.slice(0, 60)}
        {response.error.length > 60 ? "…" : ""}
      </Badge>
    );
  }
  if (response.executed) {
    return (
      <Badge color="emerald">
        Executed · {response.row_count} {response.row_count === 1 ? "row" : "rows"}
      </Badge>
    );
  }
  if (response.validated) {
    return <Badge color="sky">Validated (not executed)</Badge>;
  }
  return <Badge color="stone">Unknown state</Badge>;
}

function Badge({
  color,
  children,
}: {
  color: "emerald" | "rose" | "amber" | "sky" | "stone";
  children: React.ReactNode;
}) {
  // Tailwind doesn't track dynamic class names, so we map explicitly.
  const colorMap = {
    emerald: "bg-emerald-50 text-emerald-800 border-emerald-200",
    rose: "bg-rose-50 text-rose-800 border-rose-200",
    amber: "bg-amber-50 text-amber-800 border-amber-200",
    sky: "bg-sky-50 text-sky-800 border-sky-200",
    stone: "bg-stone-50 text-stone-700 border-stone-200",
  } as const;

  return (
    <span
      className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium border ${colorMap[color]}`}
    >
      {children}
    </span>
  );
}
