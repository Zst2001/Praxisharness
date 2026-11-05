import { requestJson } from "./transport/request";

export interface RecoveryPreview {
  session_id: string;
  workspace_root: string | null;
  workflow_id: string | null;
  run_id: string | null;
  attempt: number | null;
  checkpoint_reason: string | null;
  checkpoint_at: string | null;
  stop_reason: string | null;
  indeterminate_call_ids: string[];
  changed_files: string[];
  unchecked_files: string[];
  workspace_baseline_available: boolean;
  recovery_token: string;
  budgets: Record<string, number | null> | null;
}

export function getRecoveryPreview(sessionId: string, turnId: string): Promise<RecoveryPreview> {
  const query = new URLSearchParams({ session_id: sessionId });
  return requestJson(`/api/turns/${encodeURIComponent(turnId)}/recovery?${query.toString()}`);
}
