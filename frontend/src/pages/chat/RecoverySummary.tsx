import type { RecoveryPreview } from "../../api/recovery";

export default function RecoverySummary({ preview }: { preview: RecoveryPreview }) {
  const budget = preview.budgets;
  return <div style={{ overflowWrap: "anywhere" }}>
    <p>工作目录：{preview.workspace_root ?? "未知"}</p>
    <p>检查点：{preview.checkpoint_reason ?? "未知"} · 第 {preview.attempt ?? 1} 次尝试</p>
    {budget && <p>已用模型轮数 {budget.model_turns_used} / {budget.max_model_turns ?? "未设置"}，
      工具调用 {budget.tool_calls_used} / {budget.max_tool_calls ?? "未设置"}，
      修复 {budget.repairs_used} / {budget.max_repairs ?? "未设置"}。恢复沿用累计用量。</p>}
    {preview.indeterminate_call_ids.length > 0 && <p role="alert">以下工具结果不确定，请先检查实际影响：{preview.indeterminate_call_ids.join("、")}。系统不会自动重放这些调用。</p>}
    {preview.changed_files.length > 0 && <p role="alert">受监测文件与检查点不同：{preview.changed_files.join("、")}。</p>}
    {preview.unchecked_files.length > 0 && <p>无法完整检查：{preview.unchecked_files.join("、")}。</p>}
    {!preview.workspace_baseline_available && <p>此检查点没有文件基线，无法判断外部修改。</p>}
    <p>仅检查验收条件与文件工具涉及的最多 64 个文件，每个最多 256 KiB；命令的其他副作用需要人工核对。继续后模型仍可能提出新操作，沿用工具审批。</p>
  </div>;
}
