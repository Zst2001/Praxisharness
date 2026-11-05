export type AcceptanceCheck =
  | { id: string; kind: "file_exists"; path: string }
  | { id: string; kind: "file_contains"; path: string; text: string }
  | { id: string; kind: "json_equals"; path: string; expected: unknown }
  | { id: string; kind: "command"; cmd: string; timeout_seconds?: number };

export interface AcceptanceContract {
  checks: AcceptanceCheck[];
  max_repairs: number;
  max_model_turns?: number;
  max_tool_calls?: number;
  reserved_tool_calls?: number;
}

export type AcceptanceStatus = "pending" | "repairing" | "passed" | "failed" | "blocked" | "cancelled" | "budget_exhausted";
export interface AcceptanceReport {
  attempt: number;
  status: AcceptanceStatus;
  checks: Array<{ id: string; status: AcceptanceStatus; detail: string; duration_ms: number }>;
}
export interface AcceptanceRecord {
  schema_version: number;
  session_id?: string;
  thread_id?: string;
  verified_repairs?: Array<{ check_id: string; failed_attempt: number; contract_sha256: string }>;
  turn_id: string;
  data_idx: number;
  acceptance: { contract: AcceptanceContract; status: AcceptanceStatus; repairs: number; reports: AcceptanceReport[] } | null;
  usage: { model_turns?: number; model_calls?: number; tool_calls?: number; max_model_turns?: number; max_tool_calls?: number };
}
