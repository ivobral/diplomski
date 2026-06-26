/**
 * SchemaViewer — visual map of database tables, columns and relations.
 *
 * Each table is rendered as a card with subtle elevation, a small table
 * icon, and color-coded badges:
 *  - PK columns: amber pill on the left
 *  - FK columns: sky pill on the left + inline arrow to the referenced table
 *  - column types: muted stone pill on the right
 *
 * Cards lay out in a responsive grid and scroll independently of the rest
 * of the page so the right-side workspace (form + results) stays stable.
 */
"use client";

import type { SchemaResponse, TableDTO } from "@/lib/types";

interface Props {
  schema: SchemaResponse | null;
  loading: boolean;
}

export function SchemaViewer({ schema, loading }: Props) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white overflow-hidden shadow-sm">
      <header className="px-4 py-3 bg-gradient-to-r from-amber-50 to-stone-50 border-b border-amber-100">
        <div className="flex items-center gap-2">
          <DatabaseIcon className="text-amber-600" />
          <h3 className="text-sm font-semibold text-stone-900">
            Database schema
          </h3>
          {schema && (
            <span className="ml-auto text-xs text-stone-500 font-mono">
              {schema.tables.length}{" "}
              {schema.tables.length === 1 ? "table" : "tables"}
            </span>
          )}
        </div>
        <p className="text-xs text-stone-600 mt-1">
          Tables, columns and relations of the active database.
        </p>
      </header>

      {loading ? (
        <div className="p-6 text-sm text-stone-500 italic">Loading schema…</div>
      ) : !schema || schema.tables.length === 0 ? (
        <div className="p-6 text-sm text-stone-500 italic">
          Schema unavailable for this database.
        </div>
      ) : (
        // Scroll within the panel so a long schema doesn't stretch the page.
        <div
          className="overflow-y-auto p-3"
          style={{ maxHeight: "calc(100vh - 240px)" }}
        >
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {schema.tables.map((table) => (
              <TableCard key={table.name} table={table} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TableCard({ table }: { table: TableDTO }) {
  // Build a lookup: column name → referenced table (if FK). Lets us show
  // the relation inline next to the column instead of in a separate block.
  const fkByColumn = new Map<string, { referredTable: string; referredColumn: string }>();
  for (const fk of table.foreign_keys) {
    fk.constrained_columns.forEach((col, idx) => {
      fkByColumn.set(col, {
        referredTable: fk.referred_table,
        referredColumn: fk.referred_columns[idx] ?? fk.referred_columns[0],
      });
    });
  }

  return (
    <div className="rounded-lg border border-stone-200 bg-white overflow-hidden shadow-sm hover:shadow-md hover:border-amber-300 transition-all duration-150">
      <div className="px-3 py-2 bg-stone-50 border-b border-stone-200 flex items-center gap-2">
        <TableIcon className="text-stone-500" />
        <span className="font-mono text-sm font-semibold text-stone-900 truncate">
          {table.name}
        </span>
        <span className="ml-auto text-[10px] text-stone-500 font-mono">
          {table.columns.length} col
        </span>
      </div>

      <ul className="text-xs">
        {table.columns.map((col) => {
          const fk = fkByColumn.get(col.name);
          return (
            <li
              key={col.name}
              className="px-3 py-1.5 flex items-center gap-2 border-b border-stone-100 last:border-b-0 hover:bg-amber-50/40 transition-colors"
            >
              <KeyBadge isPrimary={col.is_primary_key} isForeign={Boolean(fk)} />
              <span className="font-mono text-stone-800 truncate flex-1 min-w-0">
                {col.name}
                {fk && (
                  <span className="text-stone-400 ml-1.5">
                    →{" "}
                    <span className="text-sky-700 font-medium">
                      {fk.referredTable}
                    </span>
                  </span>
                )}
              </span>
              <TypePill type={col.data_type} />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ---------- Small reusable atoms ---------- */

function KeyBadge({
  isPrimary,
  isForeign,
}: {
  isPrimary: boolean;
  isForeign: boolean;
}) {
  if (isPrimary) {
    return (
      <span
        className="inline-flex items-center justify-center w-6 h-4 rounded text-[9px] font-bold bg-amber-100 text-amber-800 border border-amber-200 shrink-0"
        title="Primary key"
      >
        PK
      </span>
    );
  }
  if (isForeign) {
    return (
      <span
        className="inline-flex items-center justify-center w-6 h-4 rounded text-[9px] font-bold bg-sky-100 text-sky-800 border border-sky-200 shrink-0"
        title="Foreign key"
      >
        FK
      </span>
    );
  }
  // Placeholder spacing so identifier alignment stays consistent across rows.
  return <span className="inline-block w-6 shrink-0" aria-hidden />;
}

function TypePill({ type }: { type: string }) {
  const lower = type.toLowerCase();
  // Color-code by type family — subtle hints, not loud.
  const cls = lower.includes("int")
    ? "bg-amber-50 text-amber-800 border-amber-200"
    : lower.includes("char") || lower.includes("text")
      ? "bg-stone-50 text-stone-700 border-stone-200"
      : lower.includes("numeric") || lower.includes("decimal") || lower.includes("real") || lower.includes("float")
        ? "bg-sky-50 text-sky-800 border-sky-200"
        : lower.includes("time") || lower.includes("date")
          ? "bg-emerald-50 text-emerald-800 border-emerald-200"
          : lower.includes("bool")
            ? "bg-violet-50 text-violet-800 border-violet-200"
            : "bg-stone-50 text-stone-700 border-stone-200";

  return (
    <span
      className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${cls} shrink-0 lowercase`}
    >
      {type.toLowerCase()}
    </span>
  );
}

/* ---------- Icons (inline SVG, no extra deps) ---------- */

function DatabaseIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M3 5v14a9 3 0 0 0 18 0V5" />
      <path d="M3 12a9 3 0 0 0 18 0" />
    </svg>
  );
}

function TableIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 9h18" />
      <path d="M9 3v18" />
    </svg>
  );
}
