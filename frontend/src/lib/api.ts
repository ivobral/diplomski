/**
 * Typed fetch wrapper za backend API.
 *
 * Drži sve API pozive na jednom mjestu — komponente ne moraju paziti
 * na transport detalje, error format ili URL.
 */

import type {
  DatabasesResponse,
  ProvidersResponse,
  QueryRequest,
  QueryResponse,
  SchemaResponse,
} from "@/lib/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Unificirani JSON fetch s error handling-om.
 * Backend domain errore vraća kao HTTP 400 s JSON-om ``{error, detail}``.
 */
async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.error === "string") detail = body.error;
    } catch {
      // response nije JSON — držimo se default detail-a
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

// ----- Public API -----

export async function fetchProviders(): Promise<ProvidersResponse> {
  return jsonFetch<ProvidersResponse>("/api/providers");
}

export async function fetchDatabases(): Promise<DatabasesResponse> {
  return jsonFetch<DatabasesResponse>("/api/databases");
}

export async function fetchSchema(database?: string): Promise<SchemaResponse> {
  // Backend default is "chinook"; sending it explicitly avoids any ambiguity
  // and lets us treat the path consistently across all databases.
  const qs = database ? `?database=${encodeURIComponent(database)}` : "";
  return jsonFetch<SchemaResponse>(`/api/schema${qs}`);
}

export async function executeQuery(request: QueryRequest): Promise<QueryResponse> {
  return jsonFetch<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
