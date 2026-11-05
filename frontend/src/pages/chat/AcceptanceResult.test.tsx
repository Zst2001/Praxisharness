import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AcceptanceResultCard } from "./AcceptanceResult-ui-polish";
import TurnAcceptanceResult from "./AcceptanceResult-ui-polish";
import { getTurnAcceptance } from "../../api/conversations/turns";
import { saveProjectExperience } from "../../api/memory";
import type { AcceptanceRecord } from "../../api/acceptance";
import type { RuntimeStateNode } from "../../types";

vi.mock("../../api/conversations/turns", () => ({
  getTurnAcceptance: vi.fn(),
  acceptanceDownloadUrl: () => "/api/turns/turn-a/acceptance/export?data_idx=0",
}));
vi.mock("../../api/memory", () => ({ saveProjectExperience: vi.fn() }));

const record: AcceptanceRecord = {
  schema_version: 1, turn_id: "turn-a", data_idx: 0,
  acceptance: { status: "passed", repairs: 1,
    contract: { checks: [{ id: "json", kind: "json_equals", path: "result.json", expected: { ok: true } }], max_repairs: 2, max_model_turns: 8, max_tool_calls: 16 },
    reports: [{ attempt: 1, status: "failed", checks: [{ id: "json", status: "failed", detail: "JSON 内容不匹配", duration_ms: 1 }] },
      { attempt: 2, status: "passed", checks: [{ id: "json", status: "passed", detail: "已匹配预期内容", duration_ms: 1 }] }],
  }, usage: { model_turns: 3, tool_calls: 1, max_model_turns: 4, max_tool_calls: 8 },
};

describe("Acceptance results", () => {
  it("confirms user-written notes against a verified project repair and preserves the draft on failure", async () => {
    const proof = { check_id: "json", failed_attempt: 1, contract_sha256: "a".repeat(64) };
    const scoped = { ...record, session_id: "session-a", thread_id: "thread-a", verified_repairs: [proof] };
    const view = render(<AcceptanceResultCard record={scoped} selectedCheck="json" />);
    expect(screen.queryByRole("button", { name: "保存为项目经验" })).toBeNull();
    view.rerender(<AcceptanceResultCard record={record} selectedCheck="json" projectScoped />);
    expect(screen.queryByRole("button", { name: "保存为项目经验" })).toBeNull();
    view.rerender(<AcceptanceResultCard record={scoped} selectedCheck="json" projectScoped />);
    fireEvent.click(screen.getByRole("button", { name: "保存为项目经验" }));
    const confirm = screen.getByRole("button", { name: "确认保存" });
    expect(confirm).toBeDisabled();
    expect(screen.getByLabelText("建议做法")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("适用情形"), { target: { value: "配置开关" } });
    fireEvent.change(screen.getByLabelText("建议做法"), { target: { value: "校验布尔类型" } });
    expect(saveProjectExperience).not.toHaveBeenCalled();
    vi.mocked(saveProjectExperience).mockRejectedValueOnce(new Error("请先启用记忆"));
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByText("请先启用记忆")).toBeVisible());
    expect(screen.getByLabelText("建议做法")).toHaveValue("校验布尔类型");
    fireEvent.click(confirm);
    expect(await screen.findByText("已保存为项目经验")).toBeVisible();
    expect(saveProjectExperience).toHaveBeenLastCalledWith({ ...proof, session_id: "session-a", thread_id: "thread-a",
      turn_id: record.turn_id, data_idx: 0 }, "配置开关", "校验布尔类型");
  });
  it("traces a formerly failed condition directly from its latest passing row", () => {
    const navigate = vi.fn();
    render(<AcceptanceResultCard record={record} onConditionTrace={navigate} selectedCheck="json" traceItems={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "追踪修复 · json" }));
    expect(navigate).toHaveBeenCalledWith("json");
    expect(screen.getByRole("region", { name: "条件修复轨迹" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "第 2 轮复验 · 通过" })).toBeVisible();
    expect(screen.getByText("此轮没有可关联的修复区间。")).toBeVisible();
  });
  it("shows repair history, effective budgets and a report download", () => {
    const trace = vi.fn();
    render(<AcceptanceResultCard record={record} downloadUrl="/report.json" onTrace={trace} />);
    expect(screen.getByText("修复 1/2")).toBeInTheDocument();
    expect(screen.getByText("模型轮数 3/4")).toBeInTheDocument();
    expect(screen.getByText("工具调用 1/8")).toBeInTheDocument();
    expect(screen.getByText("通过 1/1 项")).toBeInTheDocument();
    expect(screen.getByText("已匹配预期内容")).toBeInTheDocument();
    expect(screen.getByText("JSON 内容不匹配")).not.toBeVisible();
    expect(screen.getByText("最新检查 · 第 2 轮").parentElement).toHaveAttribute("open");
    expect(screen.getByText("历史检查 · 1 轮").parentElement).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("历史检查 · 1 轮"));
    fireEvent.click(screen.getByText("第 1 轮 · 未通过"));
    expect(screen.getByText("JSON 内容不匹配")).toBeVisible();
    expect(screen.getByRole("link", { name: /下载验收报告/ })).toHaveAttribute("href", "/report.json");
    fireEvent.click(screen.getByRole("button", { name: "查看执行 Trace" }));
    expect(trace).toHaveBeenCalledOnce();
  });

  it("keeps shared repair actions distinct from another condition's passing result", () => {
    const progress = record.acceptance!;
    const first = progress.reports[0].checks[0];
    const other = { ...first, id: "manifest", detail: "清单缺失" };
    render(<AcceptanceResultCard selectedCheck="json" traceItems={[]} record={{ ...record, acceptance: {
      ...progress,
      status: "blocked",
      contract: { ...progress.contract, checks: [{ id: "manifest", kind: "file_exists", path: "manifest.json" },
        { id: "preview", kind: "file_exists", path: "preview.json" }, ...progress.contract.checks] },
      reports: [{ attempt: 1, status: "failed", checks: [other, { ...other, id: "preview", status: "passed" }, first] },
        { attempt: 2, status: "blocked", checks: [{ ...other, status: "passed", detail: "清单已通过" },
          { ...other, id: "preview", status: "blocked", detail: "读取受阻" }] }],
    } }} />);
    const panel = screen.getByRole("region", { name: "条件修复轨迹" });
    expect(panel).toHaveTextContent("本轮多个失败条件共享这些操作");
    expect(panel).toHaveTextContent("尚无同一条件的下一轮复验结果");
    expect(panel).not.toHaveTextContent("清单已通过");
  });

  it("counts only latest report checks matching the contract and shows missing checks", () => {
    const progress = record.acceptance!;
    render(<AcceptanceResultCard record={{ ...record, acceptance: { ...progress, status: "failed",
      contract: { ...progress.contract, checks: [...progress.contract.checks, { id: "report", kind: "file_exists", path: "report.md" }] },
      reports: [...progress.reports, { attempt: 3, status: "failed", checks: [
        { id: "json", status: "failed", detail: "最新 JSON 失败", duration_ms: 10 },
        { id: "unknown", status: "passed", detail: "不属于契约的检查", duration_ms: 1 },
      ] }],
    } }} />);
    expect(screen.getByText("通过 0/2 项")).toBeVisible();
    expect(screen.getByText("最新检查 · 第 3 轮")).toBeVisible();
    expect(screen.getByText("最新 JSON 失败")).toBeVisible();
    expect(screen.getAllByText("未执行").filter((node) => node.closest("details")?.open)).toHaveLength(1);
    expect(screen.queryByText("不属于契约的检查")).not.toBeInTheDocument();
  });

  it("shows pending instead of a pass fraction when there is no report", () => {
    render(<AcceptanceResultCard record={{ ...record, acceptance: { ...record.acceptance!, reports: [], status: "pending" } }} />);
    expect(screen.getByText("待验收")).toBeVisible();
    expect(screen.getByText("等待首次检查")).toBeVisible();
    expect(screen.queryByText(/通过 \d+\/\d+ 项/)).not.toBeInTheDocument();
    expect(screen.queryByText(/最新检查/)).not.toBeInTheDocument();
    expect(screen.queryByText(/历史检查/)).not.toBeInTheDocument();
  });

  it.each([
    ["blocked", "检查受阻"], ["cancelled", "已取消"], ["repairing", "修复中"], ["budget_exhausted", "调用额度耗尽"],
  ] as const)("shows the %s status without hiding available check results", (status, label) => {
    render(<AcceptanceResultCard record={{ ...record, acceptance: { ...record.acceptance!, status } }} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByText("已匹配预期内容")).toBeVisible();
    expect(screen.queryByText("预算耗尽")).not.toBeInTheDocument();
  });

  it("does not render a result card without an acceptance contract", () => {
    const { container } = render(<AcceptanceResultCard record={{ ...record, acceptance: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("reads saved evidence again after remounting a completed Turn", async () => {
    vi.mocked(getTurnAcceptance).mockResolvedValue(record);
    const turn = { id: "turn-a", session_id: "session-a", thread_id: "thread-a", status: "success", current_data_idx: 0,
      data: [[{ role: "assistant", content: [{ type: "plan", status: "success", event: "acceptance_started" }] }]] } as unknown as RuntimeStateNode;
    const first = render(<TurnAcceptanceResult turn={turn} />);
    await waitFor(() => expect(screen.getByText("修复 1/2")).toBeInTheDocument());
    first.unmount();
    render(<TurnAcceptanceResult turn={turn} />);
    await waitFor(() => expect(getTurnAcceptance).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("已匹配预期内容")).toBeInTheDocument();
  });

  it("does not request acceptance for ordinary chats", () => {
    vi.mocked(getTurnAcceptance).mockClear();
    const turn = { id: "plain", status: "success", current_data_idx: 0, data: [] } as unknown as RuntimeStateNode;
    render(<TurnAcceptanceResult turn={turn} />);
    expect(getTurnAcceptance).not.toHaveBeenCalled();
  });
});
