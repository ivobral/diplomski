/**
 * Glavna stranica NL2SQL UI-a.
 *
 * Odgovorna za:
 *  - dohvat liste providera pri mount-u,
 *  - držanje state-a posljednjeg query response-a,
 *  - hook-up povijesti (localStorage),
 *  - kompozicija komponenti (QueryForm, SqlDisplay, ResultTable, …).
 *
 * Sva stranica je client-side jer trebamo interaktivnost. Server-side
 * pre-render nema benefita za alat ovog tipa.
 */
"use client";

import { useCallback, useEffect, useState } from "react";

import { ErrorPanel } from "@/components/ErrorPanel";
import { LatencyChart } from "@/components/LatencyChart";
import { QueryForm } from "@/components/QueryForm";
import { QueryHistory } from "@/components/QueryHistory";
import { ResultTable } from "@/components/ResultTable";
import { SqlDisplay } from "@/components/SqlDisplay";
import { StatusBadge } from "@/components/StatusBadge";
import { executeQuery, fetchProviders } from "@/lib/api";
import {
  appendHistory,
  clearHistory,
  loadHistory,
  type HistoryEntry,
} from "@/lib/history";
import type {
  ProviderInfo,
  ProviderName,
  QueryResponse,
  StrategyCode,
} from "@/lib/types";

export default function Home() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [defaultProvider, setDefaultProvider] = useState<ProviderName | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  // initial* props za QueryForm — mijenjaju se kad korisnik klikne na povijest.
  const [initial, setInitial] = useState<{
    question?: string;
    provider?: ProviderName;
    strategy?: StrategyCode;
  }>({});

  // Pri mount-u — dohvati popis providera i učitaj povijest.
  useEffect(() => {
    fetchProviders()
      .then((res) => {
        setProviders(res.available);
        setDefaultProvider(res.default);
      })
      .catch((err: Error) => {
        setTransportError(`Ne mogu dohvatiti listu providera: ${err.message}`);
      });

    setHistory(loadHistory());
  }, []);

  const handleSubmit = useCallback(
    async (data: {
      question: string;
      provider: ProviderName;
      strategy: StrategyCode;
    }) => {
      setLoading(true);
      setTransportError(null);
      setResponse(null);

      try {
        const result = await executeQuery(data);
        setResponse(result);
        // Spremi u povijest tek nakon uspjelog round-tripa.
        const updated = appendHistory({
          question: data.question,
          provider: data.provider,
          strategy: data.strategy,
        });
        setHistory(updated);
      } catch (err) {
        setTransportError(err instanceof Error ? err.message : "Nepoznata greška");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const handlePickHistory = useCallback((entry: HistoryEntry) => {
    setInitial({
      question: entry.question,
      provider: entry.provider,
      strategy: entry.strategy,
    });
  }, []);

  const handleClearHistory = useCallback(() => {
    clearHistory();
    setHistory([]);
  }, []);

  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-50">
      <div className="max-w-5xl mx-auto px-4 py-8 space-y-6">
        <header className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">NL2SQL</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Sustav za generiranje SQL upita iz prirodnog jezika — diplomski rad.
          </p>
        </header>

        <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <QueryForm
            providers={providers}
            defaultProvider={defaultProvider}
            loading={loading}
            initialQuestion={initial.question}
            initialProvider={initial.provider}
            initialStrategy={initial.strategy}
            onSubmit={handleSubmit}
          />
        </section>

        {transportError && (
          <div className="rounded-lg border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 p-4 text-sm text-rose-900 dark:text-rose-200">
            <strong>Transport greška:</strong> {transportError}
          </div>
        )}

        {response && (
          <>
            <section className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 space-y-2">
                <h3 className="text-sm font-semibold">Status</h3>
                <StatusBadge response={response} />
                {response.retry_count > 0 && (
                  <p className="text-xs text-zinc-500">
                    Retry pokušaji: {response.retry_count}
                  </p>
                )}
              </div>
              <LatencyChart latency={response.latency} />
            </section>

            <ErrorPanel response={response} />

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">SQL</h3>
              <SqlDisplay
                generated={response.generated_sql}
                normalized={response.normalized_sql}
              />
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Rezultat</h3>
              <ResultTable response={response} />
            </section>
          </>
        )}

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Povijest pitanja</h3>
          <QueryHistory
            entries={history}
            onPick={handlePickHistory}
            onClear={handleClearHistory}
          />
        </section>

        <footer className="text-xs text-zinc-500 pt-4 border-t border-zinc-200 dark:border-zinc-800">
          Faza 3 — frontend. Backend status:
          {" "}
          <a
            href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            Swagger UI
          </a>
          {" · "}
          <a
            href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/api/schema`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            Schema JSON
          </a>
        </footer>
      </div>
    </main>
  );
}
