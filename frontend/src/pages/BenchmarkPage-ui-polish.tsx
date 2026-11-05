import { ErrorDisplay } from "../components/ErrorDisplay";
import { ErrorAlerts } from "../components/errorFeedback";
import { useEffect, useState } from "react";
import { Alert, App, Button, Card, Col, Collapse, Dropdown, Modal, Row, Select, Spin, Statistic, Tag, Typography } from "antd";
import {
  ApiOutlined,
  CloseOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  PlayCircleOutlined,
  StopOutlined,
  TeamOutlined,
  ToolOutlined,
  DownloadOutlined,
  DeleteOutlined,
  MoreOutlined,
  AppstoreOutlined,
  AuditOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  CodeOutlined,
  BugOutlined,
  BranchesOutlined,
} from "@ant-design/icons";
import { benchmarkTraceDownloadUrl, listTasks } from "../api";
import type { BenchmarkResult, BenchmarkTaskRun, TaskInfo } from "../types";
import { isActive, useBenchmarkRuns } from "./benchmark/useBenchmarkRuns";

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中", running: "运行中", stopping: "正在停止", completed: "已完成", failed: "执行失败", cancelled: "已停止",
};
const RESOURCE_STATUS: Record<string, string> = {
  not_prepared: "未准备", preparing: "准备中", ready: "已就绪", deleting: "删除中", error: "失败",
};
const RESOURCE_PHASE: Record<string, string> = {
  queued: "等待处理", sources: "下载源码", data: "准备数据及独立依赖", image: "下载镜像", environment: "准备容器", cleanup: "清理任务资源",
  validation: "检查资源", interrupted: "操作中断", prepare: "准备资源", delete: "删除资源",
};
const PHASE_LABEL: Record<string, string> = {
  environment: "准备容器", timeout: "执行超时",
  queued: "等待执行", workspace: "准备目录", application: "初始化", agent: "执行任务", grading: "评分", cleanup: "收尾",
};
const ACTIVITY_LABEL: Record<string, string> = {
  model_request: "请求模型", model_response: "模型已响应", model_retry: "模型请求重试",
  model_delta: "接收模型输出", tool_call: "调用工具", tool_result: "工具已返回", run_finished: "执行结束",
};

const CAPABILITY_LABEL: Record<string, string> = {
  terminal: "终端任务",
  software_engineering: "软件修复",
  tool_workflow: "工具工作流",
  data_processing: "数据处理",
};

const CAPABILITY_ICON: Record<string, typeof CodeOutlined> = {
  terminal: CodeOutlined, software_engineering: BugOutlined,
  tool_workflow: BranchesOutlined, data_processing: DatabaseOutlined,
};

function OverviewRatio({ value, total, label }: { value: number; total: number; label: string }) {
  if (total <= 0) return <span className="evaluation-ratio evaluation-ratio--empty" aria-label={`${label}：暂无可计算的数据`} />;
  return <span className="evaluation-ratio" role="meter" aria-label={label}
    aria-valuemin={0} aria-valuemax={total} aria-valuenow={value} aria-valuetext={`${value} / ${total}`}>
    <span style={{ width: `${Math.min(100, Math.max(0, value / total * 100))}%` }} />
  </span>;
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "var(--muted)";
  if (score >= 0.9) return "var(--success)";
  if (score >= 0.5) return "var(--warning)";
  return "var(--error)";
}

function statusClass(status: string): string {
  if (["passed", "ready"].includes(status)) return "evaluation-status--success";
  if (["failed", "error"].includes(status)) return "evaluation-status--error";
  if (["running", "preparing", "queued"].includes(status)) return "evaluation-status--active";
  return "evaluation-status--neutral";
}

function ResultCard({ result, runId, taskRun }: { result: BenchmarkResult; runId: string; taskRun: BenchmarkTaskRun }) {
  const metrics = (result.metrics ?? {}) as Record<string, unknown>;
  const score = (result.score as number | null | undefined) ?? null;
  const verdicts = (result.verdicts ?? []) as Array<Record<string, unknown>>;
  const passed = result.passed;
  const statusLabel = score == null ? "未评分" : passed === true ? "通过" : "未通过";
  const failurePhase = typeof result.failure_phase === "string" ? result.failure_phase : "";
  const metricValue = (key: string, divisor = 1): number | "—" => {
    const value = metrics[key];
    return typeof value === "number" && Number.isFinite(value) ? value / divisor : "—";
  };

  return (
    <section className="result-card" aria-label="任务评分结果">
      <div className="result-top">
        <Statistic
          className="score"
          title="得分"
          value={score != null ? score * 100 : "未评分"}
          precision={score != null ? 0 : undefined}
          suffix={score != null ? "分" : undefined}
          styles={{ content: { color: scoreColor(score) } }}
        />
        <span className={`evaluation-status ${statusClass(score == null ? "ungraded" : passed ? "passed" : "failed")}`}>评分：{statusLabel}</span>
        {result.error && taskRun.status !== "cancelled" ? <Alert closable={{ closeIcon: <CloseOutlined />, "aria-label": "关闭错误提示" }} className="error-text" title={<ErrorDisplay error={result.error} report={result.error_report} />} type="error" showIcon /> : null}
        {failurePhase && taskRun.status !== "cancelled" ? <Typography.Text type="secondary">失败阶段：{PHASE_LABEL[failurePhase] ?? failurePhase}</Typography.Text> : null}
      </div>
      <Row className="result-metrics" gutter={[12, 12]}>
        <Col xs={12} sm={8}><Statistic prefix={<ClockCircleOutlined />} title="耗时" value={metricValue("duration_ms", 1000)} precision={1} suffix={metricValue("duration_ms") === "—" ? undefined : "s"} /></Col>
        <Col xs={12} sm={8}><Statistic prefix={<ApiOutlined />} title="模型调用" value={metricValue("model_calls")} suffix={metricValue("model_calls") === "—" ? undefined : "次"} /></Col>
        <Col xs={12} sm={8}><Statistic prefix={<ToolOutlined />} title="工具调用" value={metricValue("tool_calls")} suffix={metricValue("tool_calls") === "—" ? undefined : "次"} /></Col>
        <Col xs={12} sm={8}><Statistic prefix={<TeamOutlined />} title="子代理完成" value={metricValue("subagent_completed")} suffix={metricValue("subagent_completed") === "—" ? undefined : "个"} /></Col>
        <Col xs={12} sm={8}><Statistic prefix={<BarChartOutlined />} title="Tokens" value={metricValue("total_tokens")} /></Col>
      </Row>
      {verdicts.length > 0 ? (
        <ul className="verdicts">
          {verdicts.map((verdict, index) => {
            const ok = Number(verdict.score) >= 1;
            return (
              <li key={index} className={ok ? "ok" : "no"}>
                <Tag color={ok ? "success" : "error"}>{ok ? "✓" : "✗"}</Tag> {String(verdict.detail ?? "")}
              </li>
            );
          })}
        </ul>
      ) : null}
      {result.final_answer ? (
        <Collapse
          className="final-answer"
          size="small"
          items={[{ key: "answer", label: "最终答复", children: <pre>{String(result.final_answer)}</pre> }]}
        />
      ) : null}
      {!isActive(taskRun.status) ? (
        <Button
          className="benchmark-trace-download"
          icon={<DownloadOutlined />}
          href={benchmarkTraceDownloadUrl(runId, taskRun.id)}
          target="_blank"
          rel="noopener noreferrer"
          download
        >
          下载 Trace
        </Button>
      ) : null}
    </section>
  );
}

export default function BenchmarkPage({ active = true }: { active?: boolean }) {
  const { modal } = App.useApp();
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [capabilityFilter, setCapabilityFilter] = useState("all");
  const planner = "llm" as const;
  const benchmark = useBenchmarkRuns(active);
  const [loadError, setLoadError] = useState<Error | string | null>(null);

  useEffect(() => {
    setTasksLoading(true);
    listTasks()
      .then(setTasks)
      .catch((e) => setLoadError(e instanceof Error ? e : String(e)))
      .finally(() => setTasksLoading(false));
  }, []);

  const latest = new Map<string, { runId: string; task: BenchmarkTaskRun }>();
  for (const batch of [...benchmark.runs].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    for (const item of batch.tasks) latest.set(item.task_name, { runId: batch.id, task: item });
  }
  const anyActive = benchmark.runs.some((batch) => isActive(batch.status));
  const batch = [...benchmark.runs].reverse().find((item) => item.total > 1);
  const unavailable = !benchmark.ready || Boolean(benchmark.connectionError);
  const resources = new Map(benchmark.resources.map((item) => [item.task_name, item]));
  const allPrepared = tasks.length > 0 && tasks.every((task) => resources.get(task.name)?.status === "ready");
  const resourceBusy = benchmark.resources.some((item) => item.status === "preparing" || item.status === "deleting");
  const deleteResources = (task: TaskInfo) => {
    const confirm = modal?.confirm ?? Modal.confirm;
    confirm({
      title: `删除“${task.name}”的资源？`,
      content: "历史成绩和运行记录会保留。其他任务仍在使用的共享资源不会删除。",
      okText: "删除资源", cancelText: "取消", okButtonProps: { danger: true },
      onOk: () => benchmark.resourceAction(task.name, "delete"),
    });
  };
  const sources = [...new Set(tasks.map((task) => task.source.benchmark))];
  const visibleTasks = tasks.filter((task) =>
    (sourceFilter === "all" || task.source.benchmark === sourceFilter) &&
    (capabilityFilter === "all" || task.capability === capabilityFilter));
  const preparedCount = tasks.filter((task) => resources.get(task.name)?.status === "ready").length;
  const gradedResults = tasks.map((task) => latest.get(task.name)?.task.result).filter((result) => result?.score != null);
  const passedCount = gradedResults.filter((result) => result?.passed === true).length;
  const summaryReady = !tasksLoading && !loadError && benchmark.ready;

  return (
    <div className="benchmark-page evaluation-workbench">
      <header className="page-header">
        <div>
          <span className="evaluation-eyebrow">PRAXIS / EVALUATION</span>
          <h1>评测工作台</h1>
          <p>用真实任务检验 Agent，追踪每一次执行与评分。</p>
        </div>
        <div className="bench-toolbar">
          <div className="planner-select">
            <span className="evaluation-mode">真实模型</span>
            <span className="muted">并发上限 3 · 调用按模型计费</span>
          </div>
          <Button className="run-all" type="primary" icon={<PlayCircleOutlined />} onClick={() => void benchmark.start()} loading={benchmark.pending.has("all")} disabled={unavailable || anyActive || resourceBusy || !allPrepared || benchmark.pending.size > 0}>
            全部运行
          </Button>
        </div>
      </header>

      <section className="evaluation-overview" aria-label="评测概览">
        <dl className="evaluation-metrics">
          <div aria-label="任务总数"><dt><AppstoreOutlined aria-hidden="true" />任务总数</dt><dd>{tasksLoading || loadError ? "—" : tasks.length}</dd><dd className="evaluation-metric-caption">当前题库</dd></div>
          <div aria-label="资源就绪"><dt><DatabaseOutlined aria-hidden="true" />资源就绪</dt><dd>{summaryReady ? preparedCount : "—"}<small> / {tasksLoading || loadError ? "—" : tasks.length}</small></dd><dd className="evaluation-metric-caption">可开始执行</dd>
            <dd className="evaluation-metric-graphic"><OverviewRatio value={preparedCount} total={summaryReady ? tasks.length : 0} label="资源就绪比例" /></dd></div>
          <div aria-label="已评分任务"><dt><AuditOutlined aria-hidden="true" />已评分任务</dt><dd>{summaryReady ? gradedResults.length : "—"}<small> / {tasksLoading || loadError ? "—" : tasks.length}</small></dd><dd className="evaluation-metric-caption">未评分不计为失败</dd>
            <dd className="evaluation-metric-graphic"><OverviewRatio value={gradedResults.length} total={summaryReady ? tasks.length : 0} label="已评分任务比例" /></dd></div>
          <div aria-label="通过已评分任务"><dt><CheckCircleOutlined aria-hidden="true" />通过 / 已评分</dt><dd>{summaryReady && gradedResults.length ? passedCount : "—"}<small> / {summaryReady ? gradedResults.length : "—"}</small></dd><dd className="evaluation-metric-caption">{summaryReady && gradedResults.length ? "依据实际评分结果" : "等待首个评分结果"}</dd>
            <dd className="evaluation-metric-graphic"><OverviewRatio value={passedCount} total={summaryReady ? gradedResults.length : 0} label="已评分任务通过比例" /></dd></div>
        </dl>
      </section>

      <div className="evaluation-data-note">{benchmark.connectionError ? "连接中断，保留最近数据" : "本次服务运行 · 每项任务最新记录"}</div>

      <details className="evaluation-guide">
        <summary>如何完成一次评测</summary>
        <ol className="evaluation-workflow" aria-label="评测流程">
          <li><span className="evaluation-step">1</span><div><strong>准备任务资源</strong><span>按需下载源码、数据和环境</span></div></li>
          <li><span className="evaluation-step">2</span><div><strong>执行 Agent</strong><span>选择单项任务，或运行全部题库</span></div></li>
          <li><span className="evaluation-step">3</span><div><strong>检查结果</strong><span>查看评分与消耗，下载执行 Trace</span></div></li>
        </ol>
      </details>

      <div className="evaluation-section-label evaluation-task-heading"><h2>基准任务</h2><span>筛选仅影响展示；全部运行包含整个题库</span></div>
      <div className="benchmark-source-scores" aria-live="polite">
        {sources.map((source) => {
          const sourceTasks = tasks.filter((task) => task.source.benchmark === source);
          const results = sourceTasks.map((task) => latest.get(task.name)?.task.result);
          const graded = results.filter((result) => result?.score != null);
          const passed = graded.filter((result) => result?.passed).length;
          return <span key={source}>{source}：{passed} / {sourceTasks.length} 通过 · {graded.length} 已评分</span>;
        })}
      </div>

      {batch ? <div className="benchmark-batch" aria-live="polite">
        <strong>全部运行：{batch.finished} / {batch.total} 项已结束</strong>
        <span className={`evaluation-status ${statusClass(batch.status)}`}>{STATUS_LABEL[batch.status]}</span>
        {isActive(batch.status) ? <Button icon={<StopOutlined />} danger disabled={batch.status === "stopping" || unavailable} loading={benchmark.pending.has(`stop:${batch.id}`)} onClick={() => void benchmark.stop(batch.id)}>停止整批</Button> : null}
      </div> : null}
      {benchmark.expired ? <Alert type="warning" showIcon title="后端已重启，上次运行记录已失效。" /> : null}
      <ErrorAlerts errors={[benchmark.connectionError, benchmark.actionError, loadError]} />
      {summaryReady && !benchmark.connectionError && tasks.length > 0 && !allPrepared && !anyActive ? <div className="evaluation-preparation-note">
        <DownloadOutlined aria-hidden="true" />
        <span>还有 {tasks.length - preparedCount} 项任务的资源未就绪。点击任务卡片中的“下载资源”，准备完成后即可运行。</span>
      </div> : null}

      <div className="benchmark-filters">
        <Select aria-label="题库来源" value={sourceFilter} onChange={setSourceFilter}
          options={[{ value: "all", label: "全部来源" }, ...sources.map((value) => ({ value, label: value }))]} />
        <Select aria-label="任务类别" value={capabilityFilter} onChange={setCapabilityFilter}
          options={[{ value: "all", label: "全部类别" }, ...Object.entries(CAPABILITY_LABEL).filter(([value]) => tasks.some((task) => task.capability === value)).map(([value, label]) => ({ value, label }))]} />
        <Typography.Text type="secondary">{visibleTasks.length} / {tasks.length} 题{tasks[0]?.suite_version ? ` · ${tasks[0].suite_version}` : ""}</Typography.Text>
      </div>

      {tasksLoading ? (
        <div className="benchmark-loading"><Spin description="正在加载基准任务…" /></div>
      ) : tasks.length === 0 ? (
        <Alert type="info" showIcon title="暂无可运行的基准任务。" />
      ) : visibleTasks.length === 0 ? (
        <div className="evaluation-empty-filter"><strong>没有符合筛选条件的任务</strong><p>试试其他来源或任务类别。</p><Button onClick={() => { setSourceFilter("all"); setCapabilityFilter("all"); }}>清除筛选</Button></div>
      ) : (
        <Row className="task-grid" gutter={[16, 16]}>
          {visibleTasks.map((task) => {
            const entry = latest.get(task.name);
            const run = entry?.task;
            const active = run ? isActive(run.status) : false;
            const resource = resources.get(task.name);
            const resourcePending = benchmark.pending.has(`resources:${task.name}`);
            const busy = resourcePending || resource?.status === "preparing" || resource?.status === "deleting";
            const deletionDisabled = unavailable || active || busy || Boolean(resource?.in_use) || !resource?.has_resources || benchmark.pending.has(task.name) || benchmark.pending.has("all");
            const CapabilityIcon = CAPABILITY_ICON[task.capability] ?? AppstoreOutlined;
            return (
              <Col xs={24} lg={12} key={task.name}>
                <Card className="task-card" aria-label={`评测任务 ${task.name}`}>
                  <div className="task-head">
                    <span className="evaluation-task-symbol" aria-hidden="true"><CapabilityIcon /></span>
                    <h3 className="task-name">{task.name}</h3>
                    <Dropdown trigger={["click"]} menu={{ items: [{ key: "delete", label: "删除资源", icon: <DeleteOutlined />, danger: true, disabled: deletionDisabled }],
                      onClick: ({ key }) => { if (key === "delete" && !deletionDisabled) deleteResources(task); } }}>
                      <Button type="text" className="evaluation-resource-menu" icon={<MoreOutlined />} aria-label={`管理 ${task.name} 的资源`} title="资源管理" loading={resource?.status === "deleting"} />
                    </Dropdown>
                  </div>
                  <div className="task-badges">
                    <Tag className="capability-badge">{CAPABILITY_LABEL[task.capability] ?? task.capability}</Tag>
                    <Tag className="source-badge">{task.source.benchmark}</Tag>
                  </div>
                  <div className="evaluation-task-meta" aria-live="polite">
                    <span className={`evaluation-status ${statusClass(resource?.status ?? "not_prepared")}`}>资源：{RESOURCE_STATUS[resource?.status ?? "not_prepared"]}</span>
                    <span>{task.environment.kind === "docker" ? "Docker 环境" : "本地环境"}</span>
                    <span>{task.budgets.max_tool_calls == null ? "工具调用不限次数" : `最多 ${task.budgets.max_tool_calls} 次工具调用`}</span>
                    {!run ? <span className="evaluation-not-run">尚未运行</span> : null}
                  </div>
                  <div className="task-actions">
                    <Button type={resource?.status === "ready" ? "default" : "primary"} icon={<DownloadOutlined />} onClick={() => void benchmark.resourceAction(task.name, "prepare")}
                      loading={resource?.status === "preparing"} disabled={unavailable || active || busy || resource?.status === "ready" || benchmark.pending.has(task.name) || benchmark.pending.has("all")}>
                      下载资源
                    </Button>
                    {task.planner_modes.includes(planner) ? (
                      <Button className="evaluation-run-button" type={resource?.status === "ready" ? "primary" : "default"} icon={<PlayCircleOutlined />} onClick={() => void benchmark.start(task.name)} loading={benchmark.pending.has(task.name)} disabled={unavailable || active || busy || resource?.status !== "ready" || benchmark.pending.has("all")}>
                        运行
                      </Button>
                    ) : (
                      <span className="muted">该任务不支持 {planner} 模式</span>
                    )}
                    {(busy || resource?.status === "error") && resource ? <span className="evaluation-resource-phase" role="status">{RESOURCE_PHASE[resource.phase] ?? "处理中"}</span> : null}
                  </div>
                  {resource?.error ? <Alert className="evaluation-resource-error" closable={{ closeIcon: <CloseOutlined />, "aria-label": "关闭错误提示" }} type="error" showIcon title={<ErrorDisplay error={resource.error} report={resource.error_report} />} /> : null}
                  {run && entry ? <div className="benchmark-run-status" aria-live="polite">
                    <span className={`evaluation-status ${statusClass(run.status)}`}>状态：{STATUS_LABEL[run.status]}</span>
                    {active ? <span>{PHASE_LABEL[run.phase] ?? run.phase}</span> : null}
                    <span>{(run.duration_ms / 1000).toFixed(1)} s</span>
                    {active ? <span className="benchmark-activity">最近活动：{ACTIVITY_LABEL[run.activity] ?? PHASE_LABEL[run.activity] ?? "处理运行事件"} · {new Date(run.updated_at).toLocaleTimeString()}</span> : null}
                    {active ? <Button size="small" icon={<StopOutlined />} danger disabled={run.status === "stopping" || unavailable} loading={benchmark.pending.has(`stop:${run.id}`)} onClick={() => void benchmark.stop(entry.runId, run.id)}>停止</Button> : null}
                  </div> : null}
                  {run?.result && entry ? <ResultCard key={run.id} result={run.result} runId={entry.runId} taskRun={run} /> : null}
                  <p className="task-desc">{task.description}</p>
                  <Collapse
                    className="task-details"
                    ghost
                    size="small"
                    items={[{
                      key: "details",
                      label: "完整测试内容",
                      children: (
                        <>
                          <Typography.Paragraph>{task.description}</Typography.Paragraph>
                          <Typography.Paragraph type="secondary">难度：{task.difficulty}</Typography.Paragraph>
                          <Typography.Text strong>测试 Prompt</Typography.Text>
                          <pre className="benchmark-task-prompt">{task.prompt}</pre>
                          <Typography.Text type="secondary">
                            工具调用：{task.budgets.max_tool_calls ?? "不限次数"}
                          </Typography.Text>
                          {task.tags.length > 0 ? <div className="benchmark-task-tags">{task.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</div> : null}
                        </>
                      ),
                    }]}
                  />
                  <Collapse
                    className="task-source"
                    ghost
                    size="small"
                    items={[{
                      key: "source", label: "适配说明与许可证",
                      children: <><p>{task.source.adaptation_notes}</p><p>{task.source.license} · {task.source.source_revision}</p>
                        <a href={task.source.url} target="_blank" rel="noreferrer">{task.source.benchmark} / {task.source.task_id}</a></>,
                    }]}
                  />
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
    </div>
  );
}
