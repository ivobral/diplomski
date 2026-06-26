/**
 * Main page of the NL2SQL UI.
 *
 * Responsibilities:
 *  - fetch active provider, database catalog and schema on mount,
 *  - re-fetch schema when the user picks a different database,
 *  - hold the latest query response state,
 *  - manage query history via localStorage,
 *  - compose components (SchemaViewer, QueryForm, results, history).
 *
 * Layout: 2-column on desktop (schema on the left half, main interaction
 * on the right half). Stacks to a single column below ``lg`` breakpoint.
 */
"use client";

import { useCallback, useEffect, useState } from "react";

import { DatabasePicker } from "@/components/DatabasePicker";
import { ErrorPanel } from "@/components/ErrorPanel";
import { LatencyChart } from "@/components/LatencyChart";
import { ProviderBadge } from "@/components/ProviderBadge";
import { QueryForm } from "@/components/QueryForm";
import { QueryHistory } from "@/components/QueryHistory";
import { ResultTable } from "@/components/ResultTable";
import { SchemaViewer } from "@/components/SchemaViewer";
import { SqlDisplay } from "@/components/SqlDisplay";
import { StatusBadge } from "@/components/StatusBadge";
import {
  executeQuery,
  fetchDatabases,
  fetchProviders,
  fetchSchema,
} from "@/lib/api";
import {
  appendHistory,
  clearHistory,
  loadHistory,
  type HistoryEntry,
} from "@/lib/history";
import type {
  DatabasesResponse,
  ProvidersResponse,
  QueryResponse,
  SchemaResponse,
  StrategyCode,
} from "@/lib/types";

export default function Home() {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [providersLoading, setProvidersLoading] = useState(true);

  const [databases, setDatabases] = useState<DatabasesResponse | null>(null);
  const [databasesLoading, setDatabasesLoading] = useState(true);
  const [selectedDatabase, setSelectedDatabase] = useState<string>("chinook");

  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(true);

  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // initial* props for QueryForm — updated when the user clicks a history entry.
  const [initial, setInitial] = useState<{
    question?: string;
    strategy?: StrategyCode;
  }>({});

  // On mount: fetch providers, the database catalog, and load history.
  useEffect(() => {
    fetchProviders()
      .then(setProviders)
      .catch((err: Error) =>
        setTransportError(`Failed to load providers: ${err.message}`),
      )
      .finally(() => setProvidersLoading(false));

    fetchDatabases()
      .then((res) => {
        setDatabases(res);
        // Honor the backend's default if it differs (e.g. demo absent).
        setSelectedDatabase(res.default);
      })
      .catch((err: Error) =>
        setTransportError(`Failed to load database catalog: ${err.message}`),
      )
      .finally(() => setDatabasesLoading(false));

    setHistory(loadHistory());
  }, []);

  // Whenever the selected database changes, re-fetch its schema.
  useEffect(() => {
    setSchemaLoading(true);
    setSchema(null);
    fetchSchema(selectedDatabase)
      .then(setSchema)
      .catch((err: Error) =>
        setTransportError(`Failed to load schema: ${err.message}`),
      )
      .finally(() => setSchemaLoading(false));
  }, [selectedDatabase]);

  const handleSubmit = useCallback(
    async (data: { question: string; strategy: StrategyCode }) => {
      setLoading(true);
      setTransportError(null);
      setResponse(null);

      try {
        const result = await executeQuery({
          question: data.question,
          strategy: data.strategy,
          database: selectedDatabase,
        });
        setResponse(result);
        const updated = appendHistory({
          question: data.question,
          strategy: data.strategy,
        });
        setHistory(updated);
      } catch (err) {
        setTransportError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    },
    [selectedDatabase],
  );

  const handlePickHistory = useCallback((entry: HistoryEntry) => {
    setInitial({ question: entry.question, strategy: entry.strategy });
  }, []);

  const handleClearHistory = useCallback(() => {
    clearHistory();
    setHistory([]);
  }, []);

  return (
    <main className="min-h-screen">
      <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
        <header className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-stone-900">
              NL2SQL
            </h1>
            <p className="text-sm text-stone-600 mt-1">
              Convert natural-language questions into safely executable SQL
              queries against a relational database.
            </p>
          </div>
          <ProviderBadge providers={providers} loading={providersLoading} />
        </header>

        {/*
         * 2-column grid: schema visualization on the left half, main
         * interaction (form + results + history) on the right half.
         */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <aside className="lg:sticky lg:top-6 space-y-4">
            <DatabasePicker
              databases={databases}
              selected={selectedDatabase}
              onChange={setSelectedDatabase}
              loading={databasesLoading}
            />
            <SchemaViewer schema={schema} loading={schemaLoading} />
          </aside>

          <div className="space-y-6">
            <section className="rounded-lg border border-stone-200 bg-white p-5 shadow-sm">
              <QueryForm
                loading={loading}
                initialQuestion={initial.question}
                initialStrategy={initial.strategy}
                onSubmit={handleSubmit}
              />
            </section>

            {transportError && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
                <strong>Transport error:</strong> {transportError}
              </div>
            )}

            {response && (
              <>
                <section className="grid gap-4 md:grid-cols-2">
                  <div className="rounded-lg border border-stone-200 bg-white p-4 space-y-2">
                    <h3 className="text-sm font-semibold text-stone-900">
                      Status
                    </h3>
                    <StatusBadge response={response} />
                    {response.retry_count > 0 && (
                      <p className="text-xs text-stone-500">
                        The system automatically retried the SQL{" "}
                        {response.retry_count} time
                        {response.retry_count === 1 ? "" : "s"} before execution
                        (strategy D retry mechanism).
                      </p>
                    )}
                  </div>
                  <LatencyChart latency={response.latency} />
                </section>

                <ErrorPanel response={response} />

                <section className="space-y-2">
                  <h3 className="text-sm font-semibold text-stone-900">
                    Generated SQL
                  </h3>
                  <SqlDisplay
                    generated={response.generated_sql}
                    normalized={response.normalized_sql}
                  />
                </section>

                {response.executed && (
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold text-stone-900">
                      Query results
                    </h3>
                    <ResultTable response={response} />
                  </section>
                )}
              </>
            )}

            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-stone-900">
                Question history
              </h3>
              <QueryHistory
                entries={history}
                onPick={handlePickHistory}
                onClear={handleClearHistory}
              />
            </section>
          </div>
        </div>

        <footer className="text-xs text-stone-500 pt-4 border-t border-stone-200">
          NL2SQL — Master&apos;s thesis.
          {" · "}
          <a
            href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-amber-700 hover:text-amber-800 hover:underline"
          >
            Swagger UI
          </a>
        </footer>
      </div>
    </main>
  );
}
