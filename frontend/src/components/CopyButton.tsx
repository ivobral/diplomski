/**
 * Mali utility button koji kopira tekst u clipboard.
 * Vizualno mijenja label nakon klika (feedback korisniku).
 */
"use client";

import { useState } from "react";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      // 1.5s feedback prozor — dovoljno da korisnik primijeti "Copied!".
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API može pasti u nesigurnim kontekstima (http bez SSL).
      // Za diplomski demo lokalno (localhost) to nije problem; ne raditi
      // fallback s document.execCommand jer je deprecated.
    }
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
    >
      {copied ? "Copied!" : label}
    </button>
  );
}
