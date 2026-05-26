/**
 * QueryForm — input pitanja + provider/strategy dropdown + submit.
 *
 * Glavni interakcijski element UI-a. Drži lokalno svoj state (question,
 * provider, strategy) i emit-a `onSubmit` parent-u kad korisnik klikne.
 * Disable-anje za vrijeme pending request-a se kontrolira preko `loading`.
 */
"use client";

import { useEffect, useState } from "react";
import type { ProviderInfo, ProviderName, StrategyCode } from "@/lib/types";

interface Props {
  providers: ProviderInfo[];
  defaultProvider: ProviderName | null;
  loading: boolean;
  initialQuestion?: string;
  initialProvider?: ProviderName;
  initialStrategy?: StrategyCode;
  onSubmit: (data: { question: string; provider: ProviderName; strategy: StrategyCode }) => void;
}

const STRATEGIES: { code: StrategyCode; label: string }[] = [
  { code: "A", label: "A — samo pitanje" },
  { code: "B", label: "B — + shema" },
  { code: "C", label: "C — + relacije" },
  { code: "D", label: "D — + retry (default)" },
];

export function QueryForm({
  providers,
  defaultProvider,
  loading,
  initialQuestion = "",
  initialProvider,
  initialStrategy,
  onSubmit,
}: Props) {
  const [question, setQuestion] = useState(initialQuestion);
  const [provider, setProvider] = useState<ProviderName | null>(
    initialProvider ?? defaultProvider,
  );
  const [strategy, setStrategy] = useState<StrategyCode>(initialStrategy ?? "D");

  // Kada se props.initial* promijene (npr. klik na povijest), syncamo
  // lokalni state. useEffect je čisti pattern jer izbjegava controlled-
  // -from-props confusion.
  useEffect(() => {
    if (initialQuestion !== undefined) setQuestion(initialQuestion);
  }, [initialQuestion]);
  useEffect(() => {
    if (initialProvider) setProvider(initialProvider);
  }, [initialProvider]);
  useEffect(() => {
    if (initialStrategy) setStrategy(initialStrategy);
  }, [initialStrategy]);

  // Ako još uvijek nemamo provider (api/providers se učitava), pokušaj
  // postaviti default kad provideri stignu.
  useEffect(() => {
    if (!provider && defaultProvider) setProvider(defaultProvider);
  }, [defaultProvider, provider]);

  const canSubmit = question.trim().length > 0 && provider !== null && !loading;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !provider) return;
    onSubmit({ question: question.trim(), provider, strategy });
  };

  return (
    // suppressHydrationWarning: browser extensions (Dashlane, LastPass,
    // 1Password, Bitwarden, Grammarly) ubacuju data-* atribute u <form>
    // i <input>/<textarea> prije React hidracije. React tada javlja
    // mismatch — funkcionalno bezopasno, vizualno bučno. Suppress je
    // službena praksa za 3rd-party DOM injection.
    <form onSubmit={handleSubmit} className="space-y-3" suppressHydrationWarning>
      <textarea
        suppressHydrationWarning
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Postavi pitanje na prirodnom jeziku, npr. 'How many artists are in the database?'"
        rows={3}
        disabled={loading}
        className="w-full px-3 py-2 text-sm rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 resize-y"
        // Ctrl/Cmd + Enter za submit — bitno za demo gdje korisnik često tipka i šalje.
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            handleSubmit(e);
          }
        }}
      />

      <div className="flex flex-wrap gap-3 items-center">
        <label className="text-xs text-zinc-500">
          Provider:
          <select
            value={provider ?? ""}
            onChange={(e) => setProvider(e.target.value as ProviderName)}
            disabled={loading || providers.length === 0}
            className="ml-2 px-2 py-1 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 disabled:opacity-50"
          >
            {providers.length === 0 ? (
              <option value="">— učitavanje —</option>
            ) : (
              providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} ({p.model})
                  {p.base_url ? " ↗" : ""}
                </option>
              ))
            )}
          </select>
        </label>

        <label className="text-xs text-zinc-500">
          Strategija:
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as StrategyCode)}
            disabled={loading}
            className="ml-2 px-2 py-1 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 disabled:opacity-50"
          >
            {STRATEGIES.map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        <div className="flex-1" />

        <button
          type="submit"
          disabled={!canSubmit}
          className="px-4 py-1.5 text-sm font-medium rounded-md bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "Šaljem…" : "Submit"}
        </button>
      </div>

      <p className="text-xs text-zinc-500">
        Tip: <kbd className="px-1 py-0.5 text-xs rounded bg-zinc-100 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700">Ctrl+Enter</kbd> za submit
      </p>
    </form>
  );
}
