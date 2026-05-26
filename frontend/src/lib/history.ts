/**
 * Lokalno spremište povijesti pitanja (localStorage).
 *
 * Zašto localStorage a ne backend storage:
 * - per-user history bez auth-a (svaki browser drži svoju listu),
 * - bez backend share-anog state-a (pojednostavljuje arhitekturu),
 * - rad off-line.
 *
 * Format: lista zadnjih ~10 unosa, najnoviji prvi.
 */

import type { ProviderName, StrategyCode } from "@/lib/types";

const STORAGE_KEY = "nl2sql.history.v1";
const MAX_ENTRIES = 10;

export interface HistoryEntry {
  question: string;
  provider?: ProviderName;
  strategy?: StrategyCode;
  /** ISO timestamp — za sortiranje i prikaz "prije 5 minuta". */
  timestamp: string;
}

/** Učitaj listu iz localStorage; vraća [] ako nema ili je corrupt. */
export function loadHistory(): HistoryEntry[] {
  // SSR safety — Next.js može pozvati ovaj kod na serveru.
  if (typeof window === "undefined") return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, MAX_ENTRIES) : [];
  } catch {
    return [];
  }
}

/**
 * Dodaj novi unos. Ako identičan (ista question + provider + strategy)
 * već postoji, podigni ga na vrh umjesto duplikata.
 */
export function appendHistory(entry: Omit<HistoryEntry, "timestamp">): HistoryEntry[] {
  if (typeof window === "undefined") return [];

  const now: HistoryEntry = { ...entry, timestamp: new Date().toISOString() };
  const existing = loadHistory();

  const deduped = existing.filter(
    (e) =>
      e.question !== now.question ||
      e.provider !== now.provider ||
      e.strategy !== now.strategy,
  );

  const next = [now, ...deduped].slice(0, MAX_ENTRIES);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearHistory(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}
