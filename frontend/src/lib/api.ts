/**
 * Typed fetch wrapper za backend API.
 *
 * Drži sve API pozive na jednom mjestu da:
 * - URL i shape errora budu konzistentni,
 * - dodavanje retry-a / metrika kasnije bude jedna lokacija,
 * - komponente ne paze na transport detalje.
 */

import type { ProvidersResponse, QueryRequest, QueryResponse } from "@/lib/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Generic POST kao i GET helperi koji unifikuju error handling.
 * Backend domain errore vraća kao HTTP 400 s JSON-om {error, detail}.
 */
async function jsonFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!response.ok) {
    // Pokušaj izvući backend-ov strukturirani error; fall back na status.
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.error === "string") detail = body.error;
    } catch {
      /* response nije JSON — držimo se default detail-a */
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

// ----- Public API -----

export async function fetchProviders(): Promise<ProvidersResponse> {
  return jsonFetch<ProvidersResponse>("/api/providers");
}

export async function executeQuery(request: QueryRequest): Promise<QueryResponse> {
  return jsonFetch<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
