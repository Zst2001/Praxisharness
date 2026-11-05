import type { TurnTraceItem } from "../../types";

function fields(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

interface RepairTool {
  sequence: number;
  name: string;
  path?: string;
  operation?: string;
  status: "已返回" | "失败" | "结果未确定" | "未记录结果";
}

function contractEvents(items: TurnTraceItem[]) {
  const events: TurnTraceItem[] = [];
  let previous: Record<string, unknown> = {};
  let active = true;
  for (const entry of items) {
    if (entry.role !== "runtime" || entry.item.type !== "runtime_event") continue;
    const { event, data } = entry.item;
    if (event === "acceptance_started") {
      const current = fields(data);
      // Resume adopts the same Turn version and republishes the contract.
      const continues = current.workflow_trigger === "resume" && typeof previous.run_id === "string" &&
        current.source_run_id === previous.run_id && typeof previous.sha256 === "string" && current.sha256 === previous.sha256;
      if (!continues) events.length = 0;
      previous = current;
      active = true;
    } else if (["run_finished", "run_terminated", "run_interrupted", "run_suspended", "cancelled", "error", "run_resumed"].includes(String(event))) {
      active = false;
    } else if (event === "run_started") {
      events.length = 0;
      previous = {};
      active = true;
    } else if (active) {
      events.push(entry);
    }
  }
  return events;
}

export function repairEvidence(items: TurnTraceItem[], attempt: number) {
  const events = contractEvents(items);
  const checked = events.findIndex(({ item }) =>
    item.event === "acceptance_checked" && fields(fields(item.data).report).attempt === attempt);
  if (checked < 0) return null;
  let start: number | undefined;
  let end: number | undefined;
  let modelRequests = 0;
  const tools: RepairTool[] = [];
  const open = new Map<string, RepairTool>();
  for (const { sequence, item } of events.slice(checked + 1)) {
    const data = fields(item.data);
    if (item.event === "acceptance_repair" && start === undefined) {
      start = sequence;
      continue;
    }
    if (item.event === "acceptance_checking") {
      if (data.attempt !== attempt + 1) return null;
      end = sequence;
      break;
    }
    if (item.event === "acceptance_checked" || item.event === "acceptance_repair") return null;
    if (start === undefined) continue;
    if (item.event === "model_request") modelRequests++;
    const callId = typeof data.call_id === "string" ? data.call_id : "";
    if (item.event === "tool_call") {
      const name = typeof data.tool === "string" ? data.tool : "未知工具";
      const args = fields(data.arguments);
      const fileTool = name === "read_file" || name === "file_operation";
      const tool: RepairTool = {
        sequence, name, status: "未记录结果",
        path: fileTool && typeof args.path === "string" ? args.path : undefined,
        operation: name === "file_operation" && typeof args.operation === "string" ? args.operation : undefined,
      };
      tools.push(tool);
      if (callId) open.set(callId, tool);
    } else {
      const tool = open.get(callId);
      if (!tool) continue;
      if (item.event === "tool_result") tool.status = "已返回";
      else if (item.event === "tool_failed") tool.status = "失败";
      else if (item.event === "tool_indeterminate") tool.status = "结果未确定";
      else continue;
      open.delete(callId);
    }
  }
  return start === undefined ? null : { start, end, modelRequests, tools };
}
