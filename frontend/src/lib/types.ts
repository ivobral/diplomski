/**
 * TypeScript zrcalo Pydantic DTO-a iz backenda.
 *
 * Drži se sinkronizirano s ``backend/app/models/`` — ako se doda novi
 * field, dodaj ga i ovdje. Ručno održavanje umjesto OpenAPI generatora:
 * jedan tim, jedan endpoint set, overhead alata nije isplativ za demo.
 */

// ----- Provideri (samo za prikaz aktivnog modela, ne za odabir) -----

export type ProviderName = "anthropic" | "openai" | "ollama" | "gemini";

export interface ProviderInfo {
  name: ProviderName;
  model: string;
  /** Postavljen samo za OpenAI-kompatibilne endpoint-e (GitHub Models, OpenRouter). */
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
  // Provider se NE šalje iz UI-a (server koristi LLM_PROVIDER iz env-a).
  /** Database id — "chinook" (Postgres) or a BIRD SQLite db_id. Default chinook. */
  database?: string;
}

// ----- Databases -----

export interface DatabaseInfo {
  id: string;
  label: string;
  dialect: "postgres" | "sqlite";
  source: "demo" | "bird";
}

export interface DatabasesResponse {
  default: string;
  databases: DatabaseInfo[];
}

export interface LatencyBreakdown {
  prompt_build_ms: number | null;   // schema fetch + template formatting
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

// ----- Schema -----

export interface ColumnDTO {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
}

export interface ForeignKeyDTO {
  constrained_columns: string[];
  referred_table: string;
  referred_columns: string[];
}

export interface TableDTO {
  name: string;
  columns: ColumnDTO[];
  foreign_keys: ForeignKeyDTO[];
}

export interface SchemaResponse {
  tables: TableDTO[];
  fetched_at: number;
}
