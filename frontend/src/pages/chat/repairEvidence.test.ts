import { describe, expect, it } from "vitest";
import type { TurnTraceItem } from "../../types";
import { repairEvidence } from "./repairEvidence";

function audit(sequence: number, event: string, data: Record<string, unknown> = {}): TurnTraceItem {
  return { sequence, role: "runtime", message_idx: null, item_idx: null, completed_at: "",
    item: { type: "runtime_event", status: "success", event, data } };
}

const failed = audit(2, "acceptance_checked", { report: { attempt: 1 } });
const repair = audit(3, "acceptance_repair", { repair: 1 });

describe("repair audit window", () => {
  it("keeps a paused repair across a linked resume but isolates unrelated starts", () => {
    const start = { run_id: "original", sha256: "same-contract" };
    const resume = { run_id: "resumed", source_run_id: "original", workflow_trigger: "resume", sha256: "same-contract" };
    const before = [audit(1, "acceptance_started", start), failed, repair, audit(4, "model_request"),
      audit(5, "cancelled"), audit(6, "run_finished"), audit(7, "tool_call")];
    const after = [audit(10, "tool_call", { tool: "file_operation" }), audit(11, "acceptance_checking", { attempt: 2 })];
    expect(repairEvidence([...before, audit(9, "acceptance_started", resume), ...after], 1))
      .toMatchObject({ start: 3, end: 11, modelRequests: 1, tools: [{ sequence: 10 }] });
    for (const change of [{ workflow_trigger: "fresh" }, { source_run_id: "other" }, { sha256: "other" }]) {
      expect(repairEvidence([...before, audit(9, "acceptance_started", { ...resume, ...change }), ...after], 1)).toBeNull();
    }
  });
  it("pairs parallel results by ID, excludes both verifier phases and transcript mirrors", () => {
    const actual = repairEvidence([
      audit(1, "tool_call"), failed, repair, audit(4, "model_request"), audit(5, "model_response"),
      audit(6, "tool_call", { call_id: "a", tool: "file_operation", arguments: { operation: "write", path: "solution.py", content: "private content" } }),
      audit(7, "tool_call", { call_id: "b", tool: "run_command", arguments: { path: "unverified" } }),
      { ...audit(8, "unused"), role: "assistant", item: { type: "tool_call", status: "success" } },
      audit(9, "tool_failed", { call_id: "b" }), audit(10, "tool_result", { call_id: "a" }),
      audit(11, "acceptance_checking", { attempt: 2 }), audit(12, "tool_call"),
    ], 1);
    expect(actual).toEqual({ start: 3, end: 11, modelRequests: 1, tools: [
      { sequence: 6, name: "file_operation", operation: "write", path: "solution.py", status: "已返回" },
      { sequence: 7, name: "run_command", status: "失败" },
    ] });
  });

  it("keeps unfinished calls provisional and skips attempts without matching boundaries", () => {
    expect(repairEvidence([failed, repair, audit(4, "tool_call")], 1)).toMatchObject({
      end: undefined, tools: [{ status: "未记录结果" }],
    });
    expect(repairEvidence([failed, audit(3, "acceptance_checking", { attempt: 2 })], 1)).toBeNull();
    expect(repairEvidence([failed, repair, audit(4, "acceptance_checking", { attempt: 3 })], 1)).toBeNull();
    expect(repairEvidence([failed, repair, audit(4, "acceptance_started")], 1)).toBeNull();
    expect(repairEvidence([failed, repair], 2)).toBeNull();
    expect(repairEvidence([audit(1, "run_started"), failed, repair], 1)).toMatchObject({ start: 3 });
  });
});
