/**
 * ErrorPanel — prikaz blocked_reason ili error poruke iz response-a.
 *
 * Razdvojen od StatusBadge-a (koji daje kratak signal) jer treba prostora
 * za potencijalno duge poruke validatora (npr. nepostojeća kolona +
 * lista dostupnih). Ne renderira se ako nema greške.
 */

import type { QueryResponse } from "@/lib/types";

export function ErrorPanel({ response }: { response: QueryResponse }) {
  if (!response.blocked_reason && !response.error) return null;

  const isBlocked = Boolean(response.blocked_reason);
  const message = response.blocked_reason ?? response.error ?? "";
  const title = isBlocked ? "Sigurnosna blokada" : "Greška validacije";
  const colorClasses = isBlocked
    ? "border-rose-300 bg-rose-50 dark:border-rose-800 dark:bg-rose-950/40"
    : "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40";

  return (
    <div className={`rounded-lg border p-4 ${colorClasses}`}>
      <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-2">
        {title}
      </div>
      <p className="text-sm text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-words">
        {message}
      </p>
    </div>
  );
}
