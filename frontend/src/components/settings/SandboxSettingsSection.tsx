import { getSandboxResources, type SandboxResourceStatus, type SandboxAggregateLimits } from "../../api/settings";
import { useEffect, useState } from "react";
import { ErrorDisplay } from "../ErrorDisplay";
import { Alert, Button, Col, Form, Input, InputNumber, Row, Select, Space, Tag, Typography } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import type { SandboxLimits, SandboxNetworkRule } from "../../api";
import type { SandboxHealthState } from "../../app/useSandboxHealth";
import type { UserSettingsState } from "./useUserSettingsState";
import { brokerErrorTitle } from "./sandboxStatus";

export { brokerErrorTitle } from "./sandboxStatus";

type SectionProps = { state: UserSettingsState };

function AutoRecoveryStatus({ health }: { health: SandboxHealthState }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (
      !["observing", "verifying"].includes(health.autoRecoveryPhase)
      || health.nextRetryAt === null
    ) return undefined;
    setNow(Date.now());
    const timer = globalThis.setInterval(() => setNow(Date.now()), 1_000);
    return () => globalThis.clearInterval(timer);
  }, [health.autoRecoveryPhase, health.nextRetryAt]);

  if (health.autoRecoveryPhase === "repairing") return <Tag color="processing">正在恢复沙箱</Tag>;
  if (health.autoRecoveryPhase === "paused") return <Tag color="error">自动恢复已暂停</Tag>;
  if (health.autoRecoveryPhase === "observing" && health.nextRetryAt !== null) {
    const seconds = Math.max(0, Math.ceil((health.nextRetryAt - now) / 1_000));
    return <Tag color="warning">{seconds} 秒后自动恢复</Tag>;
  }
  if (health.autoRecoveryPhase === "verifying" && health.nextRetryAt !== null) {
    const seconds = Math.max(0, Math.ceil((health.nextRetryAt - now) / 1_000));
    return <Tag color="processing">正在验证恢复，剩余 {seconds} 秒</Tag>;
  }
  return null;
}

const limitFields: Array<{
  key: keyof SandboxLimits;
  label: string;
  min: number;
  max: number;
  hint: string;
}> = [
  { key: "wall_seconds", label: "最长运行时间（秒）", min: 1, max: 600, hint: "1–600" },
  { key: "cpu_seconds", label: "CPU 时间（秒）", min: 1, max: 300, hint: "1–300" },
  { key: "memory_mib", label: "内存（MiB）", min: 128, max: 4096, hint: "128–4096" },
  { key: "processes", label: "进程数", min: 1, max: 256, hint: "1–256" },
  { key: "handles", label: "句柄数", min: 64, max: 16384, hint: "64–16384" },
  { key: "output_chars", label: "输出字符数", min: 1000, max: 20000, hint: "1000–20000" },
  { key: "write_io_mib", label: "总写 I/O（MiB）", min: 0, max: 20480, hint: "包含文件、管道等写入；0 表示不限制" },
];

export function SandboxSettingsSection({ state }: SectionProps) {
  const settings = state.settings!;
  const config = settings.sandbox_config;
  const [resources, setResources] = useState<SandboxResourceStatus | null>(null);
  const [resourceError, setResourceError] = useState<Error | null>(null);
  const sandboxReady = state.sandboxHealth.phase === "healthy";
  useEffect(() => {
    if (!sandboxReady) { setResources(null); setResourceError(null); return; }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await getSandboxResources();
        if (!stopped) { setResources(next); setResourceError(null); }
      } catch (error) {
        if (!stopped) setResourceError(error instanceof Error ? error : new Error(String(error)));
      } finally {
        if (!stopped) timer = setTimeout(refresh, 1000);
      }
    };
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, [sandboxReady]);

  function updateAllowlist(index: number, patch: Partial<SandboxNetworkRule>) {
    state.updateSettings({
      sandbox_config: {
        ...config,
        network_allowlist: config.network_allowlist.map((rule, ruleIndex) => (
          ruleIndex === index ? { ...rule, ...patch } : rule
        )),
      },
    });
  }

  return (
    <Form layout="vertical">
      <Typography.Title level={4}>沙箱</Typography.Title>
      <Space align="center" style={{ marginBottom: 16 }} wrap>
        {state.sandboxHealth.phase === "healthy" ? <Tag color="success">沙箱已就绪</Tag> : null}
        <AutoRecoveryStatus health={state.sandboxHealth} />

      </Space>
      {state.sandboxHealth.phase === "unhealthy" ? (
        <Alert
          type="error"
          showIcon
          title={state.sandboxHealth.error_report ? undefined : brokerErrorTitle(state.sandboxHealth.code, state.sandboxHealth.installed)}
          description={(
            <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              <ErrorDisplay error={state.sandboxHealth.detail} report={state.sandboxHealth.error_report} />
            </div>
          )}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      <Form.Item label="网络权限">
        <Select
          aria-label="沙箱网络权限"
          value={config.network_mode}
          options={[
            { value: "no_network", label: "禁止网络" },
            { value: "restricted_network", label: "仅白名单" },
            { value: "full_network", label: "完整网络" },
          ]}
          onChange={(network_mode) => state.updateSettings({
            sandbox_config: { ...config, network_mode },
          })}
        />
      </Form.Item>

      <Typography.Title level={5}>网络白名单</Typography.Title>
      <Typography.Paragraph type="secondary">
        每条规则可指定端口；端口留空时允许该 IP 或域名的全部端口。仅在“仅白名单”网络模式下生效。
      </Typography.Paragraph>
      {config.network_allowlist.length === 0 ? (
        <Typography.Paragraph type="secondary">暂无白名单规则</Typography.Paragraph>
      ) : (
        <Space orientation="vertical" style={{ width: "100%", marginBottom: 12 }}>
          {config.network_allowlist.map((rule, index) => (
            <Space key={index} style={{ width: "100%" }}>
              <Input
                aria-label={`白名单主机 ${index + 1}`}
                placeholder="127.0.0.1 或 example.com"
                style={{ flex: 1 }}
                value={rule.host}
                onChange={(event) => updateAllowlist(index, { host: event.target.value })}
              />
              <InputNumber
                aria-label={`白名单端口 ${index + 1}`}
                min={1}
                max={65535}
                placeholder="全部端口"
                style={{ width: 120 }}
                value={rule.port}
                onChange={(value) => updateAllowlist(index, { port: value ?? undefined })}
              />
              <Button
                type="text"
                danger
                aria-label={`删除白名单规则 ${index + 1}`}
                icon={<DeleteOutlined />}
                onClick={() => state.updateSettings({
                  sandbox_config: {
                    ...config,
                    network_allowlist: config.network_allowlist.filter((_, ruleIndex) => ruleIndex !== index),
                  },
                })}
              />
            </Space>
          ))}
        </Space>
      )}
      <Button
        icon={<PlusOutlined />}
        disabled={config.network_allowlist.length >= 64}
        onClick={() => state.updateSettings({
          sandbox_config: {
            ...config,
            network_allowlist: [...config.network_allowlist, { host: "" }],
          },
        })}
      >
        添加白名单规则
      </Button>

      <Typography.Title level={5} style={{ marginTop: 24 }}>资源限制</Typography.Title>
      <Typography.Title level={5}>模型命令总资源限制</Typography.Title>
      <Typography.Text type="secondary">不包含右侧手动终端</Typography.Text>
      {!sandboxReady && <div>资源用量暂不可用</div>}
      {resourceError && <ErrorDisplay error={resourceError} />}
      {resources?.error_report && <ErrorDisplay report={resources.error_report} error="资源统计失败" />}
      {resources && <div>当前内存 {Math.ceil(resources.usage.memory_bytes / 1048576)} MiB · 进程 {resources.usage.processes} · 句柄 {resources.usage.handles} · 排队 {resources.queued}</div>}
      <Row gutter={[12, 0]}>
        {([['memory_mib', '总内存（MiB）'], ['processes', '总进程数'], ['handles', '总句柄数']] as const).map(([key, label]) => (
          <Col xs={24} sm={8} key={key}>
            <Form.Item label={label}>
              <InputNumber aria-label={label} min={1} precision={0} style={{ width: '100%' }}
                value={(config.aggregate_limits ?? resources?.limits)?.[key]}
                onChange={(value) => {
                  const limits: SandboxAggregateLimits | undefined = config.aggregate_limits ?? resources?.limits;
                  if (limits && value !== null) state.updateSettings({ sandbox_config: { ...config, aggregate_limits: { ...limits, [key]: value } } });
                }} />
            </Form.Item>
          </Col>
        ))}
      </Row>
      <Typography.Title level={5}>单条命令限制</Typography.Title>
      <Row data-testid="sandbox-resource-limits" gutter={[12, 0]}>
        {limitFields.map((field) => (
          <Col key={field.key} xs={24} sm={12}>
            <Form.Item label={field.label} extra={field.hint}>
              <InputNumber
                aria-label={field.label}
                min={field.min}
                max={field.max}
                precision={0}
                style={{ width: "100%" }}
                value={config.limits[field.key]}
                onChange={(value) => {
                  if (typeof value !== "number" || !Number.isInteger(value)) return;
                  state.updateSettings({
                    sandbox_config: { ...config, limits: { ...config.limits, [field.key]: value } },
                  });
                }}
              />
            </Form.Item>
          </Col>
        ))}
      </Row>
    </Form>
  );
}
