import { createRef, useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Composer, { type ComposerProps, type SettingsSelectKey } from "./Composer";
import type { FileMentionEditorHandle } from "./FileMentionEditor";
import type { FileCandidate } from "../../commands/fileCompletion";

function composerProps(overrides: Partial<ComposerProps> = {}): ComposerProps {
  return {
    input: "检查项目", busy: false, compact: false, filteredCommands: [], commandMenuVisible: false,
    activeCommandIndex: 0, mode: "agent", permissionMode: "read_only", reasoningEffort: "medium",
    todos: null, openSettingsSelect: null, editorRef: createRef<FileMentionEditorHandle>(),
    onEditorChange: vi.fn(), onKeyDown: vi.fn(), onComplete: vi.fn(), onCommandExecute: vi.fn(), onActiveCommandChange: vi.fn(),
    onModeChange: vi.fn(), onPermissionChange: vi.fn(), onReasoningChange: vi.fn(),
    onSettingsSelectChange: vi.fn(), onStop: vi.fn(), onSend: vi.fn(), fileCandidates: [],
    fileMenuVisible: false, activeFileIndex: 0, fileMenuQuery: "", onFileComplete: vi.fn(),
    onActiveFileChange: vi.fn(), onPickFiles: vi.fn(), pendingUploads: [], onRemoveUpload: vi.fn(),
    onRetryUpload: vi.fn(), onUploadPreview: vi.fn(), ...overrides,
  };
}

describe("Composer controls", () => {
  it("keeps a readable compact mode and permission alongside task execution controls", () => {
    render(<Composer {...composerProps({ compact: true, acceptanceControl: <button>验收</button> })} />);
    const environment = screen.getByRole("group", { name: "任务环境" });
    expect(within(environment).getByRole("button", { name: "运行模式：Agent" })).toHaveTextContent("Agent");
    expect(within(environment).getByRole("button", { name: "权限模式：只读" })).toHaveTextContent("只读");
    const execution = screen.getByRole("group", { name: "任务执行" });
    expect(within(execution).getByRole("button", { name: "思考等级：中" })).toHaveTextContent("思考中");
    expect(within(execution).getByRole("button", { name: "验收" })).toBeVisible();
    expect(within(execution).getByRole("button", { name: "发送" })).toBeEnabled();
    expect(screen.queryByRole("img", { name: /上下文用量/ })).not.toBeInTheDocument();
  });

  it("exposes usage only after measurements arrive and describes unknown capacity honestly", () => {
    const props = composerProps();
    const { rerender } = render(<Composer {...props} />);
    expect(screen.queryByRole("img", { name: /上下文用量/ })).not.toBeInTheDocument();
    rerender(<Composer {...props} usageTotalTokens={1250} usagePercent={25} usageContextLength={5000} />);
    expect(screen.getByRole("img", { name: "上下文用量：1,250 / 5,000 tokens" })).toHaveAttribute("tabindex", "0");
    rerender(<Composer {...props} usageTotalTokens={1250} />);
    expect(screen.getByRole("img", { name: "上下文用量：1,250 tokens" })).toBeVisible();
  });

  it.each([
    ["send", "发送", "onSend"], ["pause", "暂停", "onStop"],
    ["steer", "追加指令", "onStop"], ["resume", "继续", "onSend"],
  ] as const)("preserves the %s action callback", (actionMode, label, callback) => {
    const props = composerProps({ actionMode, submitDisabled: false });
    render(<Composer {...props} />);
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(props[callback]).toHaveBeenCalledOnce();
    expect(props[callback === "onSend" ? "onStop" : "onSend"]).not.toHaveBeenCalled();
  });

  it("honors blocked input and send controls", () => {
    const props = composerProps({ compact: true, disabled: true, submitDisabled: true });
    render(<Composer {...props} />);
    for (const name of ["上传文件", "运行模式：Agent", "权限模式：只读", "思考等级：中", "发送"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("retains compact dropdown selection behavior", async () => {
    const user = userEvent.setup();
    const props = composerProps({ compact: true });
    function Harness() {
      const [open, setOpen] = useState<SettingsSelectKey | null>(null);
      return <Composer {...props} openSettingsSelect={open} onSettingsSelectChange={setOpen} />;
    }
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "思考等级：中" }));
    await user.click(await screen.findByRole("menuitem", { name: "高（high）" }));
    expect(props.onReasoningChange).toHaveBeenCalledWith("high");
    expect(props.onSend).not.toHaveBeenCalled();
  });
});

describe("Composer completion menus", () => {
  const files: FileCandidate[] = Array.from({ length: 20 }, (_, index) => ({
    file: { name: `file-${index}.ts` } as FileCandidate["file"],
    reference: { source: "project", path: `file-${index}.ts`, display_path: `src/file-${index}.ts` },
    label: `file-${index}.ts`, sourceLabel: "项目",
  }));
  const bounds = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 800, width: 800, height: bottom - top, x: 0, y: top, toJSON: () => ({}) });

  it("opens below on the home page and caps the menu at its clipping container", () => {
    const props = composerProps({ home: true, fileMenuVisible: true, fileCandidates: files });
    const { container, rerender } = render(<div style={{ overflowY: "auto" }} data-clip><Composer {...props} /></div>);
    const clip = container.querySelector<HTMLElement>("[data-clip]")!;
    const composer = container.querySelector<HTMLElement>(".composer")!;
    vi.spyOn(clip, "getBoundingClientRect").mockReturnValue(bounds(50, 580));
    Object.defineProperty(clip, "clientHeight", { configurable: true, value: 530 });
    const anchor = vi.spyOn(composer, "getBoundingClientRect").mockReturnValue(bounds(140, 400));
    fireEvent.resize(window);
    const menu = screen.getByRole("listbox", { name: "文件补全" });
    expect(menu).toHaveAttribute("data-placement", "below");
    expect(menu).toHaveStyle({ maxHeight: "172px" });
    anchor.mockReturnValue(bounds(360, 540));
    rerender(<div style={{ overflowY: "auto" }} data-clip><Composer {...props} home={false} /></div>);
    expect(menu).toHaveAttribute("data-placement", "above");
    expect(menu).toHaveStyle({ maxHeight: "280px" });
  });

  it("moves only menu scroll when keyboard selection crosses its visible boundary", () => {
    const props = composerProps({ fileMenuVisible: true, fileCandidates: files });
    const { rerender } = render(<Composer {...props} />);
    const menu = screen.getByRole("listbox", { name: "文件补全" });
    Object.defineProperty(menu, "clientHeight", { configurable: true, value: 100 });
    const items = menu.querySelectorAll<HTMLElement>(".command-item");
    items.forEach((item, index) => {
      Object.defineProperty(item, "offsetTop", { configurable: true, value: index * 36 });
      Object.defineProperty(item, "offsetHeight", { configurable: true, value: 36 });
    });
    const pageScroll = window.scrollY;
    rerender(<Composer {...props} activeFileIndex={10} />);
    expect(menu.scrollTop).toBe(296);
    rerender(<Composer {...props} activeFileIndex={0} />);
    expect(menu.scrollTop).toBe(0);
    expect(window.scrollY).toBe(pageScroll);
    fireEvent.click(items[0]);
    expect(props.onFileComplete).toHaveBeenCalledWith(0);
  });

  it("uses the same bounded menu for slash commands without changing execution", () => {
    const props = composerProps({ home: true, commandMenuVisible: true,
      filteredCommands: [{ name: "/help", label: "帮助", description: "查看使用说明" }] });
    const { container } = render(<Composer {...props} />);
    expect(container.querySelector(".command-menu")).toHaveAttribute("data-placement", "below");
    fireEvent.click(screen.getByRole("button", { name: /\/help/ }));
    expect(props.onCommandExecute).toHaveBeenCalledWith(0);
    expect(props.onSend).not.toHaveBeenCalled();
  });
});
