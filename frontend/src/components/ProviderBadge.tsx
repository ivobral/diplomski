/**
 * ProviderBadge — read-only display of the active LLM provider + model.
 *
 * The provider is NOT picked through the UI (the dropdown was removed) —
 * in demo mode we use the single provider configured via ``LLM_PROVIDER``
 * env var. The frontend only shows which one is active so users know
 * what the results are based on.
 */
"use client";

import type { ProvidersResponse } from "@/lib/types";

interface Props {
  providers: ProvidersResponse | null;
  loading: boolean;
}

export function ProviderBadge({ providers, loading }: Props) {
  if (loading) {
    return (
      <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs bg-stone-100 text-stone-500 border border-stone-200">
        <Dot className="bg-stone-400 animate-pulse" />
        Loading provider…
      </span>
    );
  }
  if (!providers || providers.available.length === 0) {
    return (
      <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs bg-rose-50 text-rose-800 border border-rose-200">
        <Dot className="bg-rose-500" />
        No provider configured
      </span>
    );
  }

  // Active provider = backend default (settings.LLM_PROVIDER).
  const active = providers.available.find((p) => p.name === providers.default);
  if (!active) return null;

  return (
    <span
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs bg-amber-50 text-amber-900 border border-amber-200"
      title={`Backend currently using ${active.name} (${active.model})`}
    >
      <Dot className="bg-amber-500" />
      <span className="font-medium">{active.name}</span>
      <span className="text-amber-700/70 font-mono">{active.model}</span>
    </span>
  );
}

function Dot({ className }: { className?: string }) {
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${className}`} />;
}
