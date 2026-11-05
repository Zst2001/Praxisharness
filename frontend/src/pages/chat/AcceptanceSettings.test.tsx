import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AcceptanceSettings, { buildAcceptanceContract } from "./AcceptanceSettings";
import type { AcceptanceContract } from "../../api/acceptance";

describe("Task acceptance", () => {
  it("validates expected JSON without converting it to a string", () => {
    expect(buildAcceptanceContract([{ kind: "json_equals", path: "result.json", value: '{"ok":true}' }], 2).checks[0])
      .toEqual({ id: "check_1", kind: "json_equals", path: "result.json", expected: { ok: true } });
    expect(() => buildAcceptanceContract([{ kind: "json_equals", path: "result.json", value: "bad" }], 2)).toThrow("有效 JSON");
  });
  it("applies a criterion and can disable acceptance for new tasks", () => {
    const change = vi.fn();
    const { rerender } = render(<AcceptanceSettings onChange={change} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    fireEvent.change(screen.getByLabelText("条件 1 文件路径"), { target: { value: "result.txt" } });
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    const value = { max_repairs: 2, checks: [{ id: "check_1", kind: "file_exists" as const, path: "result.txt" }] };
    expect(change).toHaveBeenCalledWith(value);
    rerender(<AcceptanceSettings value={value} onChange={change} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭新任务验收" }));
    expect(change).toHaveBeenLastCalledWith(undefined);
  });
  it("prevents changing settings while the task is locked", () => {
    render(<AcceptanceSettings onChange={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: "设置任务验收" })).toBeDisabled();
  });
  it("preserves existing check identities, command timeout, and model limits when editing", () => {
    const change = vi.fn();
    const value: AcceptanceContract = {
      max_repairs: 3, max_model_turns: 17, max_tool_calls: 20, reserved_tool_calls: 2,
      checks: [
        { id: "unit_tests", kind: "command", cmd: "python -m pytest -q", timeout_seconds: 95 },
        { id: "report", kind: "file_exists", path: "report.md" },
      ],
    };
    render(<AcceptanceSettings value={value} onChange={change} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    expect(screen.getByLabelText("条件 1 超时时间")).toHaveValue("95");
    fireEvent.change(screen.getByLabelText("条件 2 文件路径"), { target: { value: "final-report.md" } });
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    expect(change).toHaveBeenCalledWith({ ...value, checks: [value.checks[0], { ...value.checks[1], path: "final-report.md" }] });
  });
  it("expands and focuses invalid advanced settings, then applies a valid reservation", async () => {
    const change = vi.fn();
    render(<AcceptanceSettings onChange={change} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    fireEvent.change(screen.getByLabelText("条件 1 文件路径"), { target: { value: "report.md" } });
    expect(screen.queryByLabelText("工具调用总上限")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /高级限制/ }));
    fireEvent.change(screen.getByLabelText("工具调用总上限"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("预留验收调用次数"), { target: { value: "13" } });
    fireEvent.click(screen.getByRole("button", { name: /高级限制/ }));
    expect(screen.getByRole("button", { name: /工具上限 12 · 验收预留 13/ })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    expect(change).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("预留验收调用次数不能超过工具调用总上限");
    expect(screen.getByRole("button", { name: /高级限制/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("预留验收调用次数")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("预留验收调用次数")).toHaveAccessibleDescription("预留验收调用次数不能超过工具调用总上限。");
    await waitFor(() => expect(screen.getByLabelText("预留验收调用次数")).toHaveFocus());
    fireEvent.change(screen.getByLabelText("预留验收调用次数"), { target: { value: "2" } });
    expect(screen.getByText(/Agent 最多使用 10 次工具调用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    expect(change).toHaveBeenCalledWith({ max_repairs: 2, max_tool_calls: 12, reserved_tool_calls: 2,
      checks: [{ id: "check_1", kind: "file_exists", path: "report.md" }] });
  });
  it("focuses new rules and gives them a unique id without renumbering surviving rules", async () => {
    const change = vi.fn();
    render(<AcceptanceSettings onChange={change} disabled={false} value={{ max_repairs: 2,
      checks: [{ id: "check_1", kind: "file_exists", path: "one.txt" }, { id: "check_2", kind: "file_exists", path: "two.txt" }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    fireEvent.click(screen.getByRole("button", { name: "删除条件 1" }));
    fireEvent.click(screen.getByRole("button", { name: "添加条件" }));
    await waitFor(() => expect(screen.getByLabelText("条件 2 文件路径")).toHaveFocus());
    fireEvent.change(screen.getByLabelText("条件 2 文件路径"), { target: { value: "three.txt" } });
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    const result: AcceptanceContract = change.mock.calls[0][0];
    expect(result.checks[0]).toEqual({ id: "check_2", kind: "file_exists", path: "two.txt" });
    expect(result.checks[1].id).not.toBe("check_2");
  });
  it("keeps unsaved advanced settings when collapsing and reopening", async () => {
    const change = vi.fn();
    const value: AcceptanceContract = { max_repairs: 2, max_model_turns: 15, max_tool_calls: 30, reserved_tool_calls: 3,
      checks: [{ id: "report", kind: "file_exists", path: "report.md" }] };
    render(<AcceptanceSettings value={value} onChange={change} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    await waitFor(() => expect(screen.getByLabelText("最多修复次数")).toBeVisible());
    expect(screen.queryByLabelText("工具调用总上限")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /高级限制/ }));
    fireEvent.change(screen.getByLabelText("预留验收调用次数"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /高级限制/ }));
    expect(screen.getByText("工具上限 30 · 验收预留 5")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /高级限制/ }));
    expect(screen.getByLabelText("预留验收调用次数")).toHaveValue("5");
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    expect(change).toHaveBeenCalledWith({ ...value, reserved_tool_calls: 5 });
  });
  it("locates a required field error and clears its error state when edited", async () => {
    render(<AcceptanceSettings onChange={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    const path = screen.getByLabelText("条件 1 文件路径");
    expect(path).toHaveAttribute("aria-invalid", "true");
    expect(path).toHaveAccessibleDescription("请填写条件 1 的文件路径。");
    await waitFor(() => expect(path).toHaveFocus());
    fireEvent.change(path, { target: { value: "output.txt" } });
    expect(path).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("locates malformed JSON without discarding the draft", async () => {
    const change = vi.fn();
    render(<AcceptanceSettings onChange={change} disabled={false} value={{ max_repairs: 1,
      checks: [{ id: "schema", kind: "json_equals", path: "data.json", expected: { ok: true } }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "设置任务验收" }));
    const json = screen.getByLabelText("条件 1 预期内容");
    fireEvent.change(json, { target: { value: "{broken" } });
    fireEvent.click(screen.getByRole("button", { name: /应\s*用/ }));
    expect(change).not.toHaveBeenCalled();
    expect(json).toHaveAttribute("aria-invalid", "true");
    expect(json).toHaveValue("{broken");
    expect(json).toHaveAccessibleDescription("条件 1 的预期内容必须是有效 JSON。");
    await waitFor(() => expect(json).toHaveFocus());
  });
  it("keeps an omitted timeout omitted and honors an explicit timeout edit", () => {
    const original = { id: "verify", kind: "command" as const, cmd: "python verify.py" };
    const draft = { kind: original.kind, path: "", value: original.cmd, original };
    expect(buildAcceptanceContract([draft], 1).checks).toEqual([original]);
    expect(buildAcceptanceContract([{ ...draft, timeoutSeconds: 60 }], 1).checks).toEqual([{ ...original, timeout_seconds: 60 }]);
  });
  it.each([
    [{ max_tool_calls: 0 }, "工具调用总上限"],
    [{ max_tool_calls: 1001 }, "工具调用总上限"],
    [{ reserved_tool_calls: -1 }, "预留验收调用次数"],
    [{ reserved_tool_calls: 0.5 }, "预留验收调用次数"],
    [{ max_tool_calls: 1, reserved_tool_calls: 2 }, "不能超过"],
  ])("validates budget constraints before creating a contract: %j", (limits, message) => {
    expect(() => buildAcceptanceContract([{ kind: "file_exists", path: "result.txt", value: "" }], 2, limits)).toThrow(message);
  });
});
