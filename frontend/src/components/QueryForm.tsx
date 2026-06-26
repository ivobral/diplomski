/**
 * QueryForm — natural-language input + submit.
 *
 * Strategy is fixed to "D" in the request (the full production pipeline);
 * strategies A/B/C exist only for the academic ablation study and aren't
 * exposed in the UI. Provider is also not picked here (the backend uses
 * the env-configured provider).
 */
"use client";

import { useEffect, useState } from "react";
import type { StrategyCode } from "@/lib/types";

interface Props {
  loading: boolean;
  initialQuestion?: string;
  initialStrategy?: StrategyCode;
  onSubmit: (data: { question: string; strategy: StrategyCode }) => void;
}

export function QueryForm({
  loading,
  initialQuestion = "",
  initialStrategy,
  onSubmit,
}: Props) {
  const [question, setQuestion] = useState(initialQuestion);

  // Re-sync when the user clicks a history entry.
  useEffect(() => {
    if (initialQuestion !== undefined) setQuestion(initialQuestion);
  }, [initialQuestion]);

  const canSubmit = question.trim().length > 0 && !loading;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    // ``initialStrategy`` from history is ignored — the form always submits D.
    void initialStrategy;
    onSubmit({ question: question.trim(), strategy: "D" });
  };

  return (
    // suppressHydrationWarning: password managers (Dashlane / LastPass /
    // Bitwarden) and Grammarly inject data-* attributes into <form> and
    // <textarea> before React hydration. Functionally harmless.
    <form onSubmit={handleSubmit} className="space-y-4" suppressHydrationWarning>
      <div>
        <label
          htmlFor="question-input"
          className="block text-sm font-medium text-stone-800 mb-1.5"
        >
          Question
        </label>
        <textarea
          id="question-input"
          suppressHydrationWarning
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. How many artists are in the database?  —  or  —  Show the top 5 selling tracks."
          rows={3}
          disabled={loading}
          className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 bg-white text-stone-900 placeholder-stone-400 focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-amber-500 disabled:opacity-50 resize-y transition-colors"
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              handleSubmit(e);
            }
          }}
        />
        <p className="mt-1 text-xs text-stone-500">
          Press{" "}
          <kbd className="px-1 py-0.5 text-[10px] rounded bg-stone-100 border border-stone-300 font-mono">
            Ctrl
          </kbd>
          {" + "}
          <kbd className="px-1 py-0.5 text-[10px] rounded bg-stone-100 border border-stone-300 font-mono">
            Enter
          </kbd>{" "}
          to submit quickly.
        </p>
      </div>

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={!canSubmit}
          className="px-5 py-2 text-sm font-medium rounded-lg bg-amber-600 text-white hover:bg-amber-700 active:bg-amber-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
        >
          {loading ? "Generating SQL…" : "Generate and run SQL"}
        </button>
      </div>
    </form>
  );
}
