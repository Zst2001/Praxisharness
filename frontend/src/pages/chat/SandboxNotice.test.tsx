import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SandboxNotice, { type SandboxNoticeHealth } from "./SandboxNotice";

const unhealthy: SandboxNoticeHealth = {
  phase: "unhealthy", code: "broker_service_not_running", installed: true,
  detail: "Windows sandbox service is stopped; exit_code=1077",
};

describe("SandboxNotice", () => {
  it("shows a light checking status and removes the notice when ready", () => {
    const { rerender } = render(<SandboxNotice health={{ phase: "checking", detail: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在检查沙箱");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    rerender(<SandboxNotice health={{ phase: "healthy", detail: null }} />);
    expect(screen.queryByRole("region", { name: "沙箱状态" })).not.toBeInTheDocument();
  });

  it("keeps diagnostic details collapsed while explaining the blocked task", async () => {
    const user = userEvent.setup();
    render(<SandboxNotice health={unhealthy} />);
    expect(screen.getByRole("status")).toHaveTextContent("沙箱服务尚未运行");
    expect(screen.getByRole("status")).toHaveTextContent("任务发送已暂停");
    expect(screen.getByText(unhealthy.detail!)).not.toBeVisible();
    await user.click(screen.getByText("查看技术详情"));
    expect(screen.getByText(unhealthy.detail!)).toBeVisible();
  });

  it("checks only on request and opens settings through its callback", async () => {
    const user = userEvent.setup();
    let finish: (() => void) | undefined;
    const check = vi.fn(() => new Promise<never>((resolve) => { finish = () => resolve(undefined as never); }));
    const settings = vi.fn();
    render(<SandboxNotice health={{ ...unhealthy, check }} onOpenSettings={settings} />);
    expect(check).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /检查状态/ }));
    fireEvent.click(screen.getByRole("button", { name: /检查状态/ }));
    expect(check).toHaveBeenCalledOnce();
    finish?.();
    await waitFor(() => expect(screen.getByRole("button", { name: /检查状态/ })).not.toHaveClass("ant-btn-loading"));
    await user.click(screen.getByRole("button", { name: /打开设置/ }));
    expect(settings).toHaveBeenCalledOnce();
  });

  it("handles a rejected check without losing the original error", async () => {
    const user = userEvent.setup();
    const check = vi.fn().mockRejectedValue(new Error("connection lost"));
    render(<SandboxNotice health={{ ...unhealthy, check }} />);
    await user.click(screen.getByRole("button", { name: /检查状态/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("状态检查未完成");
    await user.click(screen.getByText("查看技术详情"));
    expect(screen.getByText(unhealthy.detail!)).toBeVisible();
    expect(screen.getByText("connection lost")).toBeVisible();
  });

  it("does not mislabel unknown failures as a stopped or uninstalled service", () => {
    render(<SandboxNotice health={{ phase: "unhealthy", detail: "Unknown failure" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("沙箱尚未就绪");
    expect(screen.getByRole("status")).not.toHaveTextContent("未安装");
  });

  it("shows recovery in progress and retains structured error reports", async () => {
    const user = userEvent.setup();
    const check = vi.fn();
    render(<SandboxNotice health={{ ...unhealthy, autoRecoveryPhase: "repairing", check,
      error_report: { type: "SandboxError", message: "service unavailable", traceback: "diagnostic trace" },
    }} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在恢复沙箱");
    expect(screen.getByRole("button", { name: /检查状态/ })).toBeDisabled();
    await user.click(screen.getByText("查看技术详情"));
    expect(screen.getByText("SandboxError: service unavailable")).toBeVisible();
    await user.click(screen.getByText("错误详情"));
    expect(screen.getByText("diagnostic trace")).toBeVisible();
    expect(check).not.toHaveBeenCalled();
  });
});
