import { LoadingOutlined, ReloadOutlined, SafetyOutlined, SettingOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useRef, useState } from "react";
import type { SandboxHealthState } from "../../app/useSandboxHealth";
import { ErrorDisplay } from "../../components/ErrorDisplay";
import { brokerErrorTitle } from "../../components/settings/sandboxStatus";

export type SandboxNoticeHealth = Pick<SandboxHealthState, "phase" | "detail" | "error_report">
  & Partial<Pick<SandboxHealthState, "code" | "installed" | "checking" | "check" | "autoRecoveryPhase">>;

export default function SandboxNotice({ health, onOpenSettings }: {
  health: SandboxNoticeHealth;
  onOpenSettings?: () => void;
}) {
  const checkingRef = useRef(false);
  const [refreshing, setRefreshing] = useState(false);
  const [checkError, setCheckError] = useState<unknown>();
  if (health.phase === "healthy") return null;

  const recovering = health.autoRecoveryPhase === "repairing" || health.autoRecoveryPhase === "verifying";
  const checking = health.phase === "checking";
  const title = checking ? "正在检查沙箱"
    : health.autoRecoveryPhase === "repairing" ? "正在恢复沙箱"
      : health.autoRecoveryPhase === "verifying" ? "正在验证沙箱"
        : health.code || health.installed !== undefined ? brokerErrorTitle(health.code ?? null, health.installed ?? true)
          : "沙箱尚未就绪";

  async function refresh() {
    if (!health.check || checkingRef.current) return;
    checkingRef.current = true;
    setRefreshing(true);
    setCheckError(undefined);
    try { await health.check(); }
    catch (error) { setCheckError(error); }
    finally { checkingRef.current = false; setRefreshing(false); }
  }

  return <section className={`sandbox-notice sandbox-health-failure${checking || recovering ? " sandbox-notice--pending" : ""}`} aria-label="沙箱状态">
    <div className="sandbox-notice-icon" aria-hidden="true">{checking || recovering ? <LoadingOutlined /> : <SafetyOutlined />}</div>
    <div className="sandbox-notice-content">
      <div className="sandbox-notice-summary" role="status" aria-live="polite">
        <strong>{title}</strong>
        <p>{checking ? "正在确认任务执行环境，请稍候。" : "沙箱就绪前，任务发送已暂停。"}
          {health.autoRecoveryPhase === "observing" ? " 正在等待自动恢复。" : null}
          {health.autoRecoveryPhase === "paused" ? " 自动恢复已暂停，可在设置中查看沙箱状态。" : null}
        </p>
      </div>
      {checkError ? <p className="sandbox-notice-check-error" role="alert">状态检查未完成，请稍后重试。</p> : null}
      {health.detail || health.error_report || checkError ? <details className="sandbox-notice-details">
        <summary>查看技术详情</summary>
        {health.detail || health.error_report ? <ErrorDisplay error={health.detail} report={health.error_report} /> : null}
        {checkError ? <ErrorDisplay error={checkError} /> : null}
      </details> : null}
    </div>
    {!checking && (health.check || onOpenSettings) ? <div className="sandbox-notice-actions">
      {health.check ? <Button size="small" icon={<ReloadOutlined />} loading={refreshing || health.checking}
        disabled={recovering} onClick={() => void refresh()}>检查状态</Button> : null}
      {onOpenSettings ? <Button type="text" size="small" icon={<SettingOutlined />} onClick={onOpenSettings}>打开设置</Button> : null}
    </div> : null}
  </section>;
}
