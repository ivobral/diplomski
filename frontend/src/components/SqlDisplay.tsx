/**
 * SqlDisplay — sintaksno-obojan prikaz generiranog SQL-a.
 *
 * Pokazuje dvije verzije:
 *  - "Generated" — sirovi LLM output (može biti compact, bez LIMIT-a)
 *  - "Normalized" — pretty-print iz validatora + auto-LIMIT
 *
 * Toggle između dviju verzija je vrlo koristan za diplomski demo —
 * pokazuje što validator točno radi s output-om.
 */
"use client";

import { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

import { CopyButton } from "./CopyButton";

interface Props {
  generated: string | null;
  normalized: string | null;
}

type View = "normalized" | "generated";

export function SqlDisplay({ generated, normalized }: Props) {
  const hasNormalized = Boolean(normalized);

  // Default view: normalized ako postoji (pretty-print + auto-LIMIT je to
  // što je stvarno izvršeno), inače fallback na generated (slučaj blokade —
  // validator nije izračunao normalized, ali korisnik mora vidjeti što je
  // LLM generirao).
  const [view, setView] = useState<View>(hasNormalized ? "normalized" : "generated");

  // Re-sync default kad response stigne s novom shape-om (npr. nakon
  // prelaska iz blokirane → izvršene). Bez ovoga, view bi ostao zaglavljen
  // u "generated" kad korisnik šalje sljedeći uspješan upit.
  useEffect(() => {
    setView(hasNormalized ? "normalized" : "generated");
  }, [hasNormalized, generated]);

  const currentSql = (view === "normalized" ? normalized : generated) ?? "";

  // react-syntax-highlighter Prism radi vrlo dobro u prefers-color-scheme
  // dark, ali stil moramo izabrati eksplicitno. Provjeravamo media query
  // jednostavno preko `prefers-color-scheme` u CSS-u (Tailwind dark:).
  // Ovdje koristimo `oneLight` kao default jer chart ide u dark mode kroz
  // CSS dark: prefiks — biblioteke pružaju inline-stil pa to izolira
  // dark mode na sam highlighter container.

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
        <div className="flex items-center gap-1">
          <ViewTab
            active={view === "normalized"}
            disabled={!hasNormalized}
            onClick={() => setView("normalized")}
            title={hasNormalized ? "" : "Validacija nije prošla — nema normaliziranog SQL-a"}
          >
            Normalized
          </ViewTab>
          <ViewTab
            active={view === "generated"}
            onClick={() => setView("generated")}
          >
            Generated (raw)
          </ViewTab>
        </div>
        <CopyButton text={currentSql} />
      </div>

      <div className="text-sm">
        {currentSql ? (
          <>
            {/* Light theme — show only when user has light scheme */}
            <div className="dark:hidden">
              <SyntaxHighlighter
                language="sql"
                style={oneLight}
                customStyle={{ margin: 0, padding: "1rem", background: "transparent" }}
              >
                {currentSql}
              </SyntaxHighlighter>
            </div>
            {/* Dark theme — show only when user has dark scheme */}
            <div className="hidden dark:block">
              <SyntaxHighlighter
                language="sql"
                style={oneDark}
                customStyle={{ margin: 0, padding: "1rem", background: "transparent" }}
              >
                {currentSql}
              </SyntaxHighlighter>
            </div>
          </>
        ) : (
          <div className="p-4 text-zinc-500 italic">— nema SQL-a —</div>
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
          ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100"
          : "text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}
