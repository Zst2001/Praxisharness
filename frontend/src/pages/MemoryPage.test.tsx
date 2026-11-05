import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemorySettingsSection from "./MemoryPage";

const mocks = vi.hoisted(() => ({
  cancelMemoryJob: vi.fn(),
  clearMemories: vi.fn(),
  consolidateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  discoverProviderModels: vi.fn(),
  dryRunMemory: vi.fn(),
  extractMemory: vi.fn(),
  getSettings: vi.fn(),
  listMemoryEvidence: vi.fn(),
  listMemoryInjectionHistory: vi.fn(),
  listMemoryItems: vi.fn(),
  listMemoryJobs: vi.fn(),
  listSidebarThreads: vi.fn(),
  restoreMemory: vi.fn(),
  setMemoryEnabled: vi.fn(),
  updateMemoryConfig: vi.fn(),
}));

vi.mock("../api", () => mocks);

const config = {
  enabled: false,
  model_processing_enabled: false,
  disable_on_external_context: true,
  extraction_model: "",
  consolidation_model: "",
  retrieval_limit: 40,
  injection_max_items: 8,
  injection_max_tokens: 1200,
  injection_max_bytes: 8192,
};

const modelConfig = { ...config, enabled: true, model_processing_enabled: true };

const provider = {
  id: "provider_current",
  provider_name: "Local provider",
  protocol: "chat_completions",
  base_url: "http://127.0.0.1:9000/v1",
  model: "current-model",
};

const item = {
  memory_id: "memory_a",
  kind: "semantic",
  title: "Concise reports",
  content: "The user prefers concise technical reports.",
  summary: "Concise",
  scope: "global",
  project_id: null,
  confidence: 0.9,
  tags: ["preference"],
  status: "active",
  created_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-01T00:00:00+00:00",
  deleted_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getSettings.mockResolvedValue({ memory_config: config, provider_config: provider });
  mocks.discoverProviderModels.mockResolvedValue({ models: ["extract-model", "organize-model"] });
  mocks.listMemoryItems.mockResolvedValue([item]);
  mocks.listMemoryJobs.mockResolvedValue([]);
  mocks.listSidebarThreads.mockResolvedValue([]);
  mocks.listMemoryInjectionHistory.mockResolvedValue([]);
  mocks.listMemoryEvidence.mockResolvedValue([]);
  mocks.updateMemoryConfig.mockImplementation(async (value) => value);
  mocks.setMemoryEnabled.mockResolvedValue({ ...item, status: "disabled" });
  mocks.clearMemories.mockResolvedValue(undefined);
});

describe("Memory settings management", () => {
  afterEach(() => cleanup());

  it("keeps the latest evidence selection when the earlier request finishes last", async () => {
    mocks.listMemoryItems.mockResolvedValue([item, { ...item, memory_id: "second", title: "Second entry" }]);
    let resolveFirst!: (value: unknown[]) => void;
    mocks.listMemoryEvidence.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    render(<MemorySettingsSection />);
    const [first, second] = await screen.findAllByRole("button", { name: "证据" });
    fireEvent.click(first);
    fireEvent.click(second);
    await screen.findByText("Evidence · Second entry");
    await act(async () => resolveFirst([]));
    expect(screen.queryByText("Evidence · Concise reports")).not.toBeInTheDocument();
    expect(screen.getByText("Evidence · Second entry")).toBeInTheDocument();
  });

  it("enables local memory without requesting models until processing is enabled", async () => {
    const user = userEvent.setup();
    render(<MemorySettingsSection />);

    expect(await screen.findByText("Concise reports")).toBeInTheDocument();
    expect(screen.queryByText(/^任务（/)).not.toBeInTheDocument();
    expect(mocks.listMemoryJobs).not.toHaveBeenCalled();
    expect(screen.queryByText("实际注入记录")).not.toBeInTheDocument();
    expect(mocks.listMemoryInjectionHistory).not.toHaveBeenCalled();
    const memorySwitch = screen.getByRole("switch", { name: "启用记忆" });
    await user.click(memorySwitch);
    await waitFor(() => expect(mocks.updateMemoryConfig).toHaveBeenCalledWith({ ...config, enabled: true }));
    expect(mocks.discoverProviderModels).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "整理全局记忆" })).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: "允许后台模型提取与整理" }));
    await waitFor(() => expect(mocks.updateMemoryConfig).toHaveBeenCalledWith(modelConfig));
  });

  it("paginates memory items and clamps the page after refresh", async () => {
    const user = userEvent.setup();
    mocks.listMemoryItems.mockResolvedValue(Array.from({ length: 11 }, (_, index) => ({
      ...item, memory_id: "memory_" + index, title: "Entry " + (index + 1),
    })));
    render(<MemorySettingsSection />);
    expect(await screen.findByText("Entry 1")).toBeInTheDocument();
    expect(screen.queryByText("Entry 11")).not.toBeInTheDocument();
    await user.click(screen.getByTitle("2"));
    expect(await screen.findByText("Entry 11")).toBeInTheDocument();
    expect(screen.queryByText("Entry 1")).not.toBeInTheDocument();
    mocks.listMemoryItems.mockResolvedValue([item]);
    await user.click(screen.getByRole("button", { name: /刷\s*新/ }));
    expect(await screen.findByText("Concise reports")).toBeInTheDocument();
  });

  it("requires the exact confirmation before clearing", async () => {
    const user = userEvent.setup();
    render(<MemorySettingsSection />);
    await screen.findByText("Concise reports");

    await user.click(screen.getByRole("button", { name: /清空全部 Memory/ }));
    const confirm = screen.getByRole("button", { name: "永久清空" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "清空确认文字" }), "CLEAR ALL MEMORIES");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(mocks.clearMemories).toHaveBeenCalledWith("CLEAR ALL MEMORIES"));
  });

  it("automatically discovers models and saves both dropdown selections", async () => {
    mocks.getSettings.mockResolvedValue({ memory_config: modelConfig, provider_config: provider });
    const user = userEvent.setup();
    render(<MemorySettingsSection />);
    await screen.findByText("Concise reports");
    await waitFor(() => expect(mocks.discoverProviderModels).toHaveBeenCalledWith({
      config_id: provider.id,
      provider_name: provider.provider_name,
      protocol: provider.protocol,
      base_url: provider.base_url,
    }));
    expect(mocks.discoverProviderModels).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("combobox", { name: "提取模型" }));
    await user.click(document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) [title="extract-model"]')!);
    await waitFor(() => expect(mocks.updateMemoryConfig).toHaveBeenLastCalledWith({ ...modelConfig, extraction_model: "extract-model" }));

    await user.click(screen.getByRole("combobox", { name: "整理模型" }));
    await user.type(screen.getByRole("combobox", { name: "整理模型" }), "organize");
    await user.click(document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) [title="organize-model"]')!);
    await waitFor(() => expect(mocks.updateMemoryConfig).toHaveBeenLastCalledWith({
      ...modelConfig, extraction_model: "extract-model", consolidation_model: "organize-model",
    }));
  });

  it("can return an override to the current model", async () => {
    mocks.getSettings.mockResolvedValue({
      memory_config: { ...modelConfig, extraction_model: "extract-model" }, provider_config: provider,
    });
    const user = userEvent.setup();
    render(<MemorySettingsSection />);
    await screen.findByText("Concise reports");
    await user.click(screen.getByRole("combobox", { name: "提取模型" }));
    await user.click(document.querySelector('.ant-select-dropdown [title="使用当前模型（current-model）"]')!);
    await waitFor(() => expect(mocks.updateMemoryConfig).toHaveBeenLastCalledWith(modelConfig));
  });

  it("keeps saved models on discovery failure and retries on refresh", async () => {
    mocks.getSettings.mockResolvedValue({
      memory_config: { ...modelConfig, extraction_model: "saved-model" }, provider_config: provider,
    });
    mocks.discoverProviderModels.mockRejectedValueOnce(new Error("Service unavailable"));
    const user = userEvent.setup();
    render(<MemorySettingsSection />);
    expect(await screen.findByText("Service unavailable")).toBeInTheDocument();
    expect(screen.getByText("saved-model")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "启用记忆" })).toBeEnabled();
    expect(mocks.updateMemoryConfig).not.toHaveBeenCalled();

    mocks.getSettings.mockResolvedValue({ memory_config: modelConfig, provider_config: { ...provider } });
    await user.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(mocks.discoverProviderModels).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("获取模型列表失败：Service unavailable")).not.toBeInTheDocument());
  });

  it("renders project-scoped memory returned by the management API", async () => {
    mocks.listMemoryItems.mockResolvedValue([{ ...item, memory_id: "memory_project", title: "Project preference", scope: "project", project_id: "project_a" }]);
    render(<MemorySettingsSection />);

    expect(await screen.findByText("Project preference")).toBeInTheDocument();
    expect(screen.getByText("项目 project_a")).toBeInTheDocument();
  });
});
