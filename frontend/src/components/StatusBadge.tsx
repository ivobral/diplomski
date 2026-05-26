/**
 * StatusBadge — vizualni indikator stanja query response-a.
 *
 * Tri primarna stanja (mutually exclusive prema backend logici):
 *  - executed:  validacija + izvršavanje uspjeli
 *  - blocked:   safety blokada (DDL/DML/multi-statement)
 *  - error:     parse/semantic neispravan SQL (retry iscrpljen)
 */

import type { QueryResponse } from "@/lib/types";

export function StatusBadge({ response }: { response: QueryResponse }) {
  if (response.blocked_reason) {
    return (
      <Badge color="rose">
        Blokirano — sigurnosni sloj
      </Badge>
    );
  }
  if (response.error) {
    return <Badge color="amber">Greška — {response.error.slice(0, 60)}…</Badge>;
  }
  if (response.executed) {
    return <Badge color="emerald">Izvršeno · {response.row_count} redaka</Badge>;
  }
  if (response.validated) {
    return <Badge color="sky">Validirano (nije izvršeno)</Badge>;
  }
  return <Badge color="zinc">Nepoznato stanje</Badge>;
}

function Badge({
  color,
  children,
}: {
  color: "emerald" | "rose" | "amber" | "sky" | "zinc";
  children: React.ReactNode;
}) {
  // Tailwind ne pretražuje dinamičke klase, pa eksplicitno mapiramo.
  // Razlog: `bg-${color}-100` u source-u ne bi bilo detektirano u purge-u.
  const colorMap = {
    emerald: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
    rose: "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-300",
    amber: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
    sky: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-300",
    zinc: "bg-zinc-100 text-zinc-900 dark:bg-zinc-900 dark:text-zinc-300",
  } as const;

  return (
    <span
      className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${colorMap[color]}`}
    >
      {children}
    </span>
  );
}
