import { useEffect, useId, useState } from "react";
import { Button, Input, InputNumber, Modal, Select } from "antd";
import { CheckCircleOutlined, DeleteOutlined, DownOutlined, PlusOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import type { AcceptanceCheck, AcceptanceContract } from "../../api/acceptance";

type Draft = {
  kind: AcceptanceCheck["kind"];
  path: string;
  value: string;
  original?: AcceptanceCheck;
  timeoutSeconds?: number;
};
type Limits = Pick<AcceptanceContract, "max_model_turns" | "max_tool_calls" | "reserved_tool_calls">;
const emptyRule = (): Draft => ({ kind: "file_exists", path: "", value: "" });

class AcceptanceFieldError extends Error {
  constructor(readonly field: string, message: string) { super(message); }
}

function validateInteger(value: number, minimum: number, maximum: number, label: string, field: string) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new AcceptanceFieldError(field, `${label}应为 ${minimum}～${maximum} 之间的整数。`);
  }
}

export function buildAcceptanceContract(rules: Draft[], maxRepairs: number, limits: Limits = {}): AcceptanceContract {
  if (!rules.length || rules.length > 16) throw new Error("请设置 1～16 条验收条件。");
  validateInteger(maxRepairs, 0, 5, "最多修复次数", "repairs");
  const total = limits.max_tool_calls ?? 128;
  const reserved = limits.reserved_tool_calls ?? 0;
  validateInteger(total, 1, 1000, "工具调用总上限", "tool-calls");
  validateInteger(reserved, 0, 1000, "预留验收调用次数", "reserved");
  if (reserved > total) throw new AcceptanceFieldError("reserved", "预留验收调用次数不能超过工具调用总上限。");
  const ids = new Set(rules.flatMap((rule) => rule.original ? [rule.original.id] : []));
  return { ...limits, max_repairs: maxRepairs, checks: rules.map((rule, index): AcceptanceCheck => {
    let id = rule.original?.id;
    if (!id) {
      let next = index + 1;
      while (ids.has(`check_${next}`)) next += 1;
      id = `check_${next}`;
      ids.add(id);
    }
    if (rule.kind === "command") {
      if (!rule.value.trim()) throw new AcceptanceFieldError(`rule-${index}-value`, `请填写条件 ${index + 1} 的命令。`);
      const timeout = rule.timeoutSeconds ?? (rule.original?.kind === "command" ? rule.original.timeout_seconds : 30);
      if (timeout !== undefined) validateInteger(timeout, 1, 120, `条件 ${index + 1} 的超时时间`, `rule-${index}-timeout`);
      return { id, kind: rule.kind, cmd: rule.value, ...(timeout === undefined ? {} : { timeout_seconds: timeout }) };
    }
    if (!rule.path.trim()) throw new AcceptanceFieldError(`rule-${index}-path`, `请填写条件 ${index + 1} 的文件路径。`);
    const path = rule.path.trim();
    if (rule.kind === "file_exists") return { id, kind: rule.kind, path };
    if (rule.kind === "file_contains") {
      if (!rule.value) throw new AcceptanceFieldError(`rule-${index}-value`, `请填写条件 ${index + 1} 的预期文本。`);
      return { id, kind: rule.kind, path, text: rule.value };
    }
    try { return { id, kind: rule.kind, path, expected: JSON.parse(rule.value) }; }
    catch { throw new AcceptanceFieldError(`rule-${index}-value`, `条件 ${index + 1} 的预期内容必须是有效 JSON。`); }
  }) };
}

export default function AcceptanceSettings({ value, onChange, disabled }: {
  value?: AcceptanceContract;
  onChange: (value: AcceptanceContract | undefined) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<Draft[]>([emptyRule()]);
  const [repairs, setRepairs] = useState(2);
  const [toolCalls, setToolCalls] = useState(128);
  const [reservedCalls, setReservedCalls] = useState(0);
  const [original, setOriginal] = useState<AcceptanceContract>();
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<AcceptanceFieldError>();
  const [focusRequest, setFocusRequest] = useState<{ field: string }>();
  const instanceId = useId();
  const fieldId = (field: string) => `${instanceId}-acceptance-${field}`;
  useEffect(() => {
    if (!open || !focusRequest) return;
    const timer = setTimeout(() => document.getElementById(`${instanceId}-acceptance-${focusRequest.field}`)?.focus(), 0);
    return () => clearTimeout(timer);
  }, [open, focusRequest, advanced, instanceId]);
  function fieldProps(field: string) {
    return { id: fieldId(field), "aria-invalid": error?.field === field,
      "aria-describedby": error?.field === field ? `${fieldId(field)}-error` : undefined,
      status: error?.field === field ? "error" as const : undefined };
  }
  function fieldError(field: string) {
    return error?.field === field ? <p className="acceptance-field-error" id={`${fieldId(field)}-error`} role="alert">{error.message}</p> : null;
  }
  function edit() {
    setRules(value?.checks.map((check) => ({ kind: check.kind, original: check,
      path: "path" in check ? check.path : "",
      value: check.kind === "command" ? check.cmd : check.kind === "file_contains" ? check.text
        : check.kind === "json_equals" ? JSON.stringify(check.expected, null, 2) : "",
    })) ?? [emptyRule()]);
    setRepairs(value?.max_repairs ?? 2);
    setToolCalls(value?.max_tool_calls ?? 128);
    setReservedCalls(value?.reserved_tool_calls ?? 0);
    setOriginal(value);
    setAdvanced(false);
    setFocusRequest(undefined);
    setError(undefined);
    setOpen(true);
  }
  function update(index: number, changes: Partial<Draft>) {
    setRules((current) => current.map((rule, position) => position === index ? { ...rule, ...changes } : rule));
    if (error?.field.startsWith(`rule-${index}-`)) setError(undefined);
  }
  function save() {
    if (disabled) return;
    try {
      const limits: Limits = {};
      if (original?.max_model_turns !== undefined) limits.max_model_turns = original.max_model_turns;
      if (original?.max_tool_calls !== undefined || toolCalls !== 128) limits.max_tool_calls = toolCalls;
      if (original?.reserved_tool_calls !== undefined || reservedCalls !== 0) limits.reserved_tool_calls = reservedCalls;
      onChange(buildAcceptanceContract(rules, repairs, limits));
      setOpen(false);
    }
    catch (failure) {
      const next = failure instanceof AcceptanceFieldError ? failure : new AcceptanceFieldError("form", (failure as Error).message);
      setError(next);
      if (next.field === "tool-calls" || next.field === "reserved") setAdvanced(true);
      setFocusRequest({ field: next.field });
    }
  }
  return <>
    <Button type="text" size="small" className={`acceptance-trigger${value ? " acceptance-trigger--active" : ""}`}
      icon={<CheckCircleOutlined />} disabled={disabled} aria-label="设置任务验收" onClick={edit}>
      {value ? `验收 ${value.checks.length}` : "验收"}
    </Button>
    {open ? <Modal className="acceptance-modal" title={<span className="acceptance-modal-title"><SafetyCertificateOutlined aria-hidden="true" /> 任务验收</span>}
      open onCancel={() => setOpen(false)} onOk={save} okText="应用" okButtonProps={{ disabled }} cancelText="取消" width={700}>
      <div className="acceptance-intro">
        <p>先约定完成标准，再让 Agent 执行。</p>
        <span>应用于接下来新建的任务，运行中的条件保持固定。</span>
      </div>
      <section className="acceptance-section" aria-labelledby="acceptance-checks-heading">
        <div className="acceptance-section-heading">
          <h3 id="acceptance-checks-heading">完成标准</h3><span>{rules.length} / 16 条</span>
        </div>
        <p className="acceptance-section-note">所有条件通过后，任务才会标记为验收通过。</p>
        <div className="acceptance-rules">
          {rules.map((rule, index) => <div className="acceptance-rule" key={index}>
            <div className="acceptance-rule-heading">
              <span className="acceptance-rule-index">条件 {index + 1}</span>
              <Select aria-label={`条件 ${index + 1} 类型`} value={rule.kind} disabled={disabled}
                onChange={(kind) => update(index, { kind, value: "", timeoutSeconds: undefined })}
                options={[{ value: "file_exists", label: "文件存在" }, { value: "file_contains", label: "包含指定文本" }, { value: "json_equals", label: "JSON 内容匹配" }, { value: "command", label: "命令成功退出" }]} />
              <Button type="text" icon={<DeleteOutlined />} aria-label={`删除条件 ${index + 1}`} disabled={disabled || rules.length === 1}
                onClick={() => { setRules((current) => current.filter((_, position) => position !== index)); setError(undefined); }} />
            </div>
            {rule.kind !== "command" ? <div><Input {...fieldProps(`rule-${index}-path`)} aria-label={`条件 ${index + 1} 文件路径`} placeholder="工作区内的文件路径，例如 result.json"
              value={rule.path} disabled={disabled} maxLength={4096} onChange={(event) => update(index, { path: event.target.value })} />{fieldError(`rule-${index}-path`)}</div> : null}
            {rule.kind !== "file_exists" ? <div><Input.TextArea {...fieldProps(`rule-${index}-value`)} aria-label={`条件 ${index + 1} 预期内容`} rows={3} maxLength={16000}
              placeholder={rule.kind === "command" ? "例如 python -m pytest -q（使用当前终端语法）" : rule.kind === "json_equals" ? "填写完整预期 JSON" : "填写必须包含的文本"}
              value={rule.value} disabled={disabled} onChange={(event) => update(index, { value: event.target.value })} />{fieldError(`rule-${index}-value`)}</div> : null}
            {rule.kind === "command" ? <div className="acceptance-command-options">
              <label htmlFor={fieldId(`rule-${index}-timeout`)}>超时（秒）</label>
              <InputNumber {...fieldProps(`rule-${index}-timeout`)} aria-label={`条件 ${index + 1} 超时时间`} min={1} max={120} precision={0} disabled={disabled}
                value={rule.timeoutSeconds ?? (rule.original?.kind === "command" ? rule.original.timeout_seconds : undefined) ?? 30}
                onChange={(seconds) => update(index, { timeoutSeconds: seconds ?? 30 })} />
              <span>遵循当前权限和沙箱设置</span>
              {fieldError(`rule-${index}-timeout`)}
            </div> : null}
          </div>)}
        </div>
        <Button type="dashed" className="acceptance-add" icon={<PlusOutlined aria-hidden="true" />} disabled={disabled || rules.length >= 16}
          onClick={() => { setRules((current) => [...current, emptyRule()]); setFocusRequest({ field: `rule-${rules.length}-path` }); }}>添加条件</Button>
      </section>
      <section className="acceptance-section acceptance-repair-setting">
        <div><label htmlFor={fieldId("repairs")}>最多修复次数</label><p className="acceptance-section-note">检查未通过时继续修复，0 表示仅检查。</p></div>
        <InputNumber {...fieldProps("repairs")} aria-label="最多修复次数" min={0} max={5} precision={0} value={repairs} disabled={disabled}
          onChange={(number) => { setRepairs(number ?? 0); if (error?.field === "repairs") setError(undefined); }} />
        {fieldError("repairs")}
      </section>
      <section className="acceptance-section acceptance-advanced">
        <button type="button" className="acceptance-advanced-toggle" aria-expanded={advanced} aria-controls={fieldId("advanced")}
          onClick={() => setAdvanced((current) => !current)}>
          <span>高级限制</span><span className="acceptance-advanced-summary">工具上限 {toolCalls} · 验收预留 {reservedCalls}</span><DownOutlined aria-hidden="true" />
        </button>
        {advanced ? <div id={fieldId("advanced")} className="acceptance-advanced-content">
        <div className="acceptance-limits">
          <div className="acceptance-limit">
            <label htmlFor={fieldId("tool-calls")}>工具调用总上限</label>
            <InputNumber {...fieldProps("tool-calls")} aria-label="工具调用总上限" min={1} max={1000} precision={0} value={toolCalls} disabled={disabled}
              onChange={(number) => { setToolCalls(number ?? 128); if (error?.field === "tool-calls" || error?.field === "reserved") setError(undefined); }} />
            {fieldError("tool-calls")}
            <span>包括执行、检查与修复使用的工具。</span>
          </div>
          <div className="acceptance-limit">
            <label htmlFor={fieldId("reserved")}>预留验收调用次数</label>
            <InputNumber {...fieldProps("reserved")} aria-label="预留验收调用次数" min={0} max={1000} precision={0} value={reservedCalls} disabled={disabled}
              onChange={(number) => { setReservedCalls(number ?? 0); if (error?.field === "reserved") setError(undefined); }} />
            {fieldError("reserved")}
            <span>从总上限中预留，0 表示不预留。</span>
          </div>
        </div>
        <p className={`acceptance-budget-summary${reservedCalls > toolCalls ? " acceptance-budget-summary--invalid" : ""}`} aria-live="polite">
          {reservedCalls > toolCalls ? "预留次数不能超过总上限，请调整后应用。" : reservedCalls > 0
            ? `Agent 最多使用 ${toolCalls - reservedCalls} 次工具调用，其余 ${reservedCalls} 次留给验收。到达执行上限时，尝试完成最后一轮检查。`
            : "验收与执行共享工具调用额度。预留调用次数可减少任务在检查前耗尽额度的情况。"}
        </p>
        <p className="acceptance-section-note">实际执行还受系统运行上限约束。调用次数不等于模型费用上限。</p>
        </div> : null}
      </section>
      {fieldError("form")}
      <div className="acceptance-modal-bottom">
        <span>每轮检查结果可在任务 Trace 中查看。</span>
        {value ? <Button danger type="text" disabled={disabled} onClick={() => { onChange(undefined); setOpen(false); }}>关闭新任务验收</Button> : null}
      </div>
    </Modal> : null}
  </>;
}
