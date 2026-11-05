import { ArrowRightOutlined, NodeIndexOutlined, ReloadOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { HarnessDiagram, TaskIllustration } from "./WorkbenchIllustrations-ui-polish";

export const WORKBENCH_STARTERS = [
  { key: "explore", label: "了解项目", description: "梳理结构，找到关键入口", prompt: "请先阅读当前项目，梳理目录结构、核心模块和运行方式，先不要修改文件。" },
  { key: "implement", label: "实现想法", description: "拆解需求，验证交付结果", prompt: "我想在当前项目实现一个新功能。请先了解现有实现，再和我确认需求、验收条件与实施方案。" },
  { key: "investigate", label: "排查问题", description: "复现问题，用测试验证修复", prompt: "请帮我排查当前项目的问题。先了解相关代码和测试，等我提供具体的复现步骤后再修改，并用测试验证修复结果。" },
] as const;

interface WorkbenchWelcomeProps {
  disabled?: boolean;
  onSelectPrompt?: (prompt: string) => void;
  heroOnly?: boolean;
}

export default function WorkbenchWelcome({ disabled, onSelectPrompt, heroOnly }: WorkbenchWelcomeProps) {
  return <section className="welcome workbench-welcome" aria-label="Praxis 工作台">
    <div className="workbench-intro">
      <h1>开始一项新任务<span>.</span></h1>
      <p>描述目标与完成标准，让交付有迹可循。</p>
    </div>
    {!heroOnly ? <><WorkbenchGuide /><WorkbenchStarters disabled={disabled} onSelectPrompt={onSelectPrompt} /></> : null}
  </section>;
}

export function WorkbenchGuide() {
  return <details className="harness-guide">
    <summary><NodeIndexOutlined aria-hidden="true" /><span className="harness-guide-heading"><strong>Harness 如何工作</strong><small>验收未通过时返回分析</small></span><HarnessDiagram /></summary>
    <div className="harness-flow" aria-label="任务验收流程">
      <ol className="harness-flow-steps">
        <li><span className="harness-step-number">01</span><div><strong>定义任务</strong><p>明确目标，配置验收条件。</p></div></li>
        <li><span className="harness-step-number">02</span><div><strong>分析任务</strong><p>根据目标选择做法。修复时，先分析未通过的原因。</p></div></li>
        <li><span className="harness-step-number">03</span><div><strong>执行方案</strong><p>在权限范围内调用工具，生成或修正交付物。</p></div></li>
        <li><span className="harness-step-number">04</span><div><strong>验收结果</strong><p>逐项检查交付物。全部通过后结束，有失败项则在剩余额度内返回分析。</p></div></li>
      </ol>
      <p className="harness-repair-loop"><ReloadOutlined aria-hidden="true" />配置验收后启用；达到修复或调用上限即停止。</p>
    </div>
  </details>;
}

export function WorkbenchStarters({ disabled, onSelectPrompt }: WorkbenchWelcomeProps) {
  const [selected, setSelected] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  function select(key: string, prompt: string) {
    if (disabled || !onSelectPrompt) return;
    onSelectPrompt(prompt);
    setSelected(key);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setSelected(""), 2400);
  }
  return <section className="workbench-starters" aria-label="任务建议">
    <div className="workbench-section-label"><h2>选择一个起点</h2><span>点击填入，再补充你的需求</span></div>
    <div className="workbench-prompt-grid">
      {WORKBENCH_STARTERS.map((item) => <button key={item.key} type="button"
        className={`workbench-prompt-button${selected === item.key ? " is-inserted" : ""}`}
        disabled={disabled || !onSelectPrompt} onClick={() => select(item.key, item.prompt)}>
        <span className="workbench-prompt-visual"><TaskIllustration variant={item.key} /></span>
        <span className="workbench-prompt-copy"><strong>{item.label}</strong><span>{item.description}</span></span>
        <ArrowRightOutlined className="workbench-prompt-arrow" aria-hidden="true" />
      </button>)}
    </div>
    <p className="workbench-welcome-note" role="status" aria-live="polite">
      {selected ? "已填入建议，可在输入框中继续补充。" : "建议只会填入输入框，由你确认后发送。"}
    </p>
  </section>;
}
