/**
 * TypeScript zrcalo Pydantic DTO-a iz backenda (app/models/query.py i
 * app/api/providers.py). Drži se sinkronizirano s backend-om — ako se
 * doda novi field, dodaj ga i ovdje.
 *
 * Razlog za ručno održavanje umjesto auto-generiranja iz OpenAPI specs:
 * jedan endpoint, jedan team — overhead alata (orval, openapi-typescript)
 * nije isplativ za diplomski demo.
 */

// ----- Provideri -----

export type ProviderName = "anthropic" | "openai" | "ollama" | "gemini";

export interface ProviderInfo {
  name: ProviderName;
  model: string;
  /** Postavljen samo za "openai" kad ide na alternativni endpoint
   *  (GitHub Models, OpenRouter, Groq). Frontend može prikazati uz ime. */
  base_url: string | null;
}

export interface ProvidersResponse {
  default: ProviderName;
  available: ProviderInfo[];
}

// ----- Query -----

export type StrategyCode = "A" | "B" | "C" | "D";

export interface QueryRequest {
  question: string;
  strategy?: StrategyCode;
  provider?: ProviderName;
}

export interface LatencyBreakdown {
  llm_ms: number | null;
  validation_ms: number | null;
  execution_ms: number | null;
  total_ms: number | null;
}

export interface QueryResponse {
  question: string;
  generated_sql: string | null;
  normalized_sql: string | null;
  validated: boolean;
  executed: boolean;
  error: string | null;
  blocked_reason: string | null;
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
  row_count: number;
  latency: LatencyBreakdown;
  retry_count: number;
}
