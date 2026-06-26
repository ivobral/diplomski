/**
 * ErrorPanel — display blocked_reason or error message from the response.
 *
 * Kept separate from StatusBadge (which is a short signal) because
 * validator messages can be long (e.g. a list of available columns
 * as suggestions). Does not render when there is no error.
 */

import type { QueryResponse } from "@/lib/types";

export function ErrorPanel({ response }: { response: QueryResponse }) {
  if (!response.blocked_reason && !response.error) return null;

  const isBlocked = Boolean(response.blocked_reason);
  const message = response.blocked_reason ?? response.error ?? "";
  const title = isBlocked ? "Security block" : "Validation error";
  const hint = isBlocked
    ? "The validator refused to execute this SQL because it is not pure SELECT (e.g. DROP, DELETE, INSERT, or multi-statement). This is the intended behaviour of the security layer."
    : "The generated SQL failed parsing or semantic checks. Try rephrasing your question.";

  const containerCls = isBlocked
    ? "border-rose-200 bg-rose-50"
    : "border-orange-200 bg-orange-50";

  return (
    <div className={`rounded-lg border p-4 ${containerCls}`}>
      <div className="text-sm font-semibold text-stone-900 mb-1.5">{title}</div>
      <p className="text-sm text-stone-700 whitespace-pre-wrap break-words font-mono">
        {message}
      </p>
      <p className="mt-2 text-xs text-stone-600">{hint}</p>
    </div>
  );
}
