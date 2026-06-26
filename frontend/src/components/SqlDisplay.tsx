/**
 * SqlDisplay — sintaksno-obojan prikaz generiranog SQL-a.
 *
 * Pokazuje dvije verzije s toggle:
 *  - "Normalized" — pretty-print iz validatora + auto-LIMIT (ono što se
 *    zaista izvršilo nad bazom)
 *  - "Generated (raw)" — sirovi LLM output (može biti kompaktan, bez LIMIT-a)
 *
 * Toggle je koristan za demo: pokazuje što validator zapravo radi s
 * output-om LLM-a (cleanup + LIMIT enforcement).
 */
"use client";

import { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

import { CopyButton } from "./CopyButton";

interface Props {
  generated: string | null;
  normalized: string | null;
}

type View = "normalized" | "generated";

export function SqlDisplay({ generated, normalized }: Props) {
  const hasNormalized = Boolean(normalized);

  // Default view: normalized ako postoji. Razlog: to je SQL koji je
  // stvarno izvršen (s auto-LIMIT-om). Fallback na generated samo kad
  // validator nije izračunao normalized (npr. blokirano DDL-om).
  const [view, setView] = useState<View>(hasNormalized ? "normalized" : "generated");

  // Re-sync default kad stigne novi response s drugačijim stanjem.
  useEffect(() => {
    setView(hasNormalized ? "normalized" : "generated");
  }, [hasNormalized, generated]);

  const currentSql = (view === "normalized" ? normalized : generated) ?? "";

  return (
    <div className="rounded-lg border border-stone-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-amber-50 border-b border-amber-100">
        <div className="flex items-center gap-1">
          <ViewTab
            active={view === "normalized"}
            disabled={!hasNormalized}
            onClick={() => setView("normalized")}
            title={
              hasNormalized
                ? "Pretty-printed + auto-LIMIT from the validator (this is what actually ran)"
                : "Validation failed — no normalized SQL is available"
            }
          >
            Normalized
          </ViewTab>
          <ViewTab
            active={view === "generated"}
            onClick={() => setView("generated")}
            title="Raw SQL as produced by the LLM, before validator cleanup"
          >
            Generated (raw)
          </ViewTab>
        </div>
        <CopyButton text={currentSql} />
      </div>

      <div className="text-sm">
        {currentSql ? (
          <SyntaxHighlighter
            language="sql"
            style={oneLight}
            customStyle={{
              margin: 0,
              padding: "1rem",
              background: "transparent",
              fontSize: "0.85rem",
            }}
          >
            {currentSql}
          </SyntaxHighlighter>
        ) : (
          <div className="p-4 text-stone-500 italic">— no SQL —</div>
        )}
      </div>
    </div>
  );
}

function ViewTab({
  active,
  onClick,
  disabled,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`text-xs px-3 py-1 rounded-md transition-colors ${
        active
          ? "bg-amber-200 text-amber-900"
          : "text-stone-700 hover:bg-amber-100"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}
