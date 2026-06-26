/**
 * DatabasePicker — prominent dropdown choice of the active database.
 *
 * Rendered as its own card above the SchemaViewer so it's immediately
 * visible — without this card users had no idea they could switch
 * between Chinook and BIRD databases. Selecting a value:
 *  - re-fetches the schema for that database (page-level handler),
 *  - changes routing in POST /api/query (Chinook vs BIRD pipeline).
 */
"use client";

import type { DatabaseInfo, DatabasesResponse } from "@/lib/types";

interface Props {
  databases: DatabasesResponse | null;
  selected: string;
  onChange: (databaseId: string) => void;
  loading?: boolean;
}

export function DatabasePicker({
  databases,
  selected,
  onChange,
  loading,
}: Props) {
  const demos = databases?.databases.filter((d) => d.source === "demo") ?? [];
  const bird = databases?.databases.filter((d) => d.source === "bird") ?? [];

  // Description of the currently-selected database (for the helper text).
  const active = databases?.databases.find((d) => d.id === selected);

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-2">
        <ServerIcon className="text-amber-600" />
        <label
          htmlFor="database-select"
          className="text-sm font-semibold text-stone-900"
        >
          Active database
        </label>
      </div>

      <select
        id="database-select"
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading || !databases}
        className="w-full text-sm font-mono px-3 py-2 rounded-lg border-2 border-amber-300 bg-white text-stone-900 focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-amber-500 hover:border-amber-400 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-wait"
      >
        {loading || !databases ? (
          <option>Loading…</option>
        ) : (
          <>
            {demos.length > 0 && (
              <optgroup label="Demo database">
                {demos.map(renderOption)}
              </optgroup>
            )}
            {bird.length > 0 && (
              <optgroup label="BIRD benchmark (SQLite)">
                {bird.map(renderOption)}
              </optgroup>
            )}
          </>
        )}
      </select>

      <p className="text-xs text-stone-600 mt-2 leading-relaxed">
        {active ? (
          <>
            Querying{" "}
            <span className="font-semibold text-stone-800">{active.label}</span>{" "}
            via{" "}
            <span className="font-mono text-amber-800">{active.dialect}</span>
            {active.source === "bird" && (
              <> — from the BIRD Mini-Dev benchmark.</>
            )}
          </>
        ) : (
          <>Pick a database to query.</>
        )}
      </p>
    </div>
  );
}

function renderOption(db: DatabaseInfo) {
  return (
    <option key={db.id} value={db.id}>
      {db.label}
    </option>
  );
}

function ServerIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <rect x="2" y="2" width="20" height="8" rx="2" />
      <rect x="2" y="14" width="20" height="8" rx="2" />
      <line x1="6" y1="6" x2="6.01" y2="6" />
      <line x1="6" y1="18" x2="6.01" y2="18" />
    </svg>
  );
}
