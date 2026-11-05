import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { RuntimeStateNode, TurnTraceItem } from "../../types";
import { repairEvidence } from "./repairEvidence";
import SaveProjectExperience from "./SaveProjectExperience";
import type { AcceptanceContract, AcceptanceRecord, AcceptanceReport, AcceptanceStatus } from "../../api/acceptance";
import { acceptanceDownloadUrl, getTurnAcceptance } from "../../api/conversations/turns";

const labels: Record<AcceptanceStatus, string> = {
  pending: "待验收", repairing: "修复中", passed: "通过", failed: "未通过",
  blocked: "检查受阻", cancelled: "已取消", budget_exhausted: "调用额度耗尽",
};

const checkLabels = { file_exists: "文件存在", file_contains: "包含指定文本", json_equals: "JSON 内容匹配", command: "命令成功退出" };
function statusColor(status?: AcceptanceStatus) {
  return status === "passed" ? "green" : status === "failed" || status === "budget_exhausted" ? "red"
    : status === "blocked" || status === "repairing" ? "orange" : undefined;
}

interface ConditionNavigation {
  projectScoped?: boolean;
  onConditionTrace?: (checkId: string) => void;
  selectedCheck?: string;
  traceItems?: TurnTraceItem[];
}

function RepairHistory({ record, checkId, items, projectScoped }: {
  record: AcceptanceRecord; checkId: string; items?: TurnTraceItem[]; projectScoped?: boolean;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [checkId]);
  const progress = record.acceptance;
  const rule = progress?.contract.checks.find((check) => check.id === checkId);
  if (!progress || !rule) return null;
  return <section className="acceptance-repair-history" aria-label="条件修复轨迹">
    <h3 ref={heading} tabIndex={-1}>修复轨迹 · {checkId}</h3>
    <code>{rule.kind === "command" ? rule.cmd : rule.path}</code>
    {progress.reports.map((report) => {
      const failure = report.checks.find((check) => check.id === checkId && check.status === "failed");
      if (!failure) return null;
      const evidence = items && repairEvidence(items, report.attempt);
      const nextReport = progress.reports.find((candidate) => candidate.attempt === report.attempt + 1);
      const next = nextReport?.checks.find((check) => check.id === checkId);
      const receipt = record.verified_repairs?.find((repair) => repair.check_id === checkId && repair.failed_attempt === report.attempt);
      return <article key={report.attempt}>
        <h4>第 {report.attempt} 轮未通过</h4><p>{failure.detail}</p>
        <h4>修复期间的操作</h4>
        {report.checks.filter((check) => check.status === "failed").length > 1 && <p>本轮多个失败条件共享这些操作。</p>}
        {evidence ? <>
          <p>Trace #{evidence.start}{evidence.end !== undefined ? ` → #${evidence.end}` : " 起（尚无复验开始记录）"}
            {" · "}已记录 {evidence.modelRequests} 次模型请求，{evidence.tools.length} 次工具调用</p>
          {evidence.tools.length > 0 && <ol>{evidence.tools.map((tool) => <li key={tool.sequence}>
            <code>#{tool.sequence} {tool.name} {tool.operation} {tool.path}</code> · {tool.status}
          </li>)}</ol>}
        </> : <p>{items ? "此轮没有可关联的修复区间。" : "等待 Trace 数据。"}</p>}
        <h4>{next ? `第 ${report.attempt + 1} 轮复验 · ${labels[next.status]}` : "尚无同一条件的下一轮复验结果"}</h4>
        {next && <p>{next.detail}</p>}
        {projectScoped && receipt && record.session_id && record.thread_id && <SaveProjectExperience
          key={JSON.stringify([record.turn_id, record.data_idx, checkId, report.attempt, receipt.contract_sha256])}
          source={{ ...receipt, session_id: record.session_id, thread_id: record.thread_id,
            turn_id: record.turn_id, data_idx: record.data_idx }} />}
      </article>;
    })}
  </section>;
}

function ReportChecks({ report, contract, onConditionTrace, reports }: {
  report: AcceptanceReport; contract: AcceptanceContract;
  onConditionTrace?: (checkId: string) => void; reports?: AcceptanceReport[];
}) {
  return <ol className="acceptance-result-checks">{contract.checks.map((rule, index) => {
    const result = report.checks.find((check) => check.id === rule.id);
    return <li key={rule.id} className="acceptance-result-check">
      <div className="acceptance-result-check-heading"><strong>条件 {index + 1} · {checkLabels[rule.kind]}</strong>
        <Tag color={statusColor(result?.status)}>{result ? labels[result.status] : "未执行"}</Tag></div>
      <code className="acceptance-result-target">{rule.kind === "command" ? rule.cmd : rule.path}</code>
      <p className="acceptance-result-detail">{result?.detail ?? "本轮没有该条件的检查结果。"}</p>
      <span className="acceptance-result-check-meta">{rule.id}{result ? ` · ${Math.round(result.duration_ms)} ms` : ""}</span>
      {onConditionTrace && reports?.some((round) => round.checks.some((check) => check.id === rule.id && check.status === "failed"))
        && <Button type="link" className="acceptance-repair-link" aria-label={`追踪修复 · ${rule.id}`}
          onClick={() => onConditionTrace(rule.id)}>追踪修复 →</Button>}
    </li>;
  })}</ol>;
}

export function AcceptanceResultCard({ record, downloadUrl, onTrace, onConditionTrace, selectedCheck, traceItems, projectScoped }: {
  record: AcceptanceRecord; downloadUrl?: string; onTrace?: () => void;
} & ConditionNavigation) {
  const progress = record.acceptance;
  if (!progress) return null;
  const policy = progress.contract;
  const latest = progress.reports[progress.reports.length - 1];
  const history = progress.reports.slice(0, -1);
  const passed = latest ? policy.checks.filter((rule) => latest.checks.find((check) => check.id === rule.id)?.status === "passed").length : 0;
  const status = latest ? progress.status : "pending";
  return <Card className="acceptance-result-card" size="small" title="任务验收结果" aria-label="任务验收结果">
    <div className="acceptance-result-summary" aria-live="polite">
      <Tag color={statusColor(status)}>{labels[status]}</Tag>
      <strong>{latest ? `通过 ${passed}/${policy.checks.length} 项` : "等待首次检查"}</strong>
      <span>{latest ? `第 ${latest.attempt} 轮检查结果` : `${policy.checks.length} 条完成标准`}</span>
    </div>
    {latest && policy.checks.length > 0 ? <div className={`acceptance-result-meter${passed === policy.checks.length ? " is-complete" : ""}`} aria-hidden="true">
      <span style={{ width: `${passed / policy.checks.length * 100}%` }} />
    </div> : null}
    <div className="acceptance-result-usage" aria-label="任务用量">
      <span>修复 {progress.repairs}/{policy.max_repairs}</span>
      <span>模型轮数 {record.usage.model_turns ?? "—"}/{record.usage.max_model_turns ?? policy.max_model_turns ?? 32}</span>
      <span>工具调用 {record.usage.tool_calls ?? "—"}/{record.usage.max_tool_calls ?? policy.max_tool_calls ?? 128}</span>
    </div>
    {selectedCheck && <RepairHistory record={record} checkId={selectedCheck} items={traceItems} projectScoped={projectScoped} />}
    {latest ? <details key={`latest-${latest.attempt}`} className="acceptance-result-report" open>
      <summary>最新检查 · 第 {latest.attempt} 轮</summary>
      <ReportChecks report={latest} contract={policy} reports={progress.reports} onConditionTrace={onConditionTrace} />
    </details> : <p className="acceptance-result-empty">任务尚未进行最终验收，检查完成后将在这里显示结果。</p>}
    {history.length ? <details className="acceptance-result-history">
      <summary>历史检查 · {history.length} 轮</summary>
      {history.slice().reverse().map((report) => <details className="acceptance-result-report" key={report.attempt}>
        <summary>第 {report.attempt} 轮 · {labels[report.status]}</summary><ReportChecks report={report} contract={policy} />
      </details>)}
    </details> : null}
    <div className="acceptance-result-actions">
      {downloadUrl ? <Button size="small" icon={<DownloadOutlined aria-hidden="true" />} href={downloadUrl} download>下载验收报告</Button> : null}
      {onTrace ? <Button size="small" onClick={onTrace}>查看执行 Trace</Button> : null}
    </div>
  </Card>;
}

export default function TurnAcceptanceResult({ turn, dataIdx = turn.current_data_idx, onTrace, ...navigation }: {
  turn: RuntimeStateNode; dataIdx?: number; onTrace?: () => void;
} & ConditionNavigation) {
  const [record, setRecord] = useState<AcceptanceRecord | null>(null);
  const [error, setError] = useState<string>();
  const available = turn.data[dataIdx]?.some((message) => message.content.some((item) => String(item.event ?? "").startsWith("acceptance_"))) ?? false;
  useEffect(() => {
    setRecord(null); setError(undefined);
    if (!available) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const next = await getTurnAcceptance(turn.session_id, turn.thread_id, turn.id, dataIdx, controller.signal);
        if (!controller.signal.aborted) { setRecord(next); setError(undefined); }
      } catch (failure) {
        if (!controller.signal.aborted) setError((failure as Error).message);
      } finally {
        if (!controller.signal.aborted && turn.status === "running") timer = setTimeout(load, 2000);
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [available, turn.id, turn.session_id, turn.thread_id, turn.status, dataIdx]);
  if (error) return <Alert type="error" title="验收结果暂时无法加载" description={error} />;
  return record?.turn_id === turn.id && record.data_idx === dataIdx ? <AcceptanceResultCard record={record} onTrace={onTrace} {...navigation}
    downloadUrl={acceptanceDownloadUrl(turn.session_id, turn.thread_id, turn.id, dataIdx)} /> : null;
}
