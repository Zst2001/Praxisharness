import { ApiOutlined, SafetyCertificateOutlined, SettingOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import type { ProviderConfig } from "../../api/settings";
import type { SandboxHealthPhase } from "../../app/useSandboxHealth";

export default function WorkbenchEnvironment({ provider, sandboxPhase, onOpenSettings }: {
  provider?: ProviderConfig | null;
  sandboxPhase: SandboxHealthPhase;
  onOpenSettings?: () => void;
}) {
  const hasModel = Boolean(provider?.model);
  return <section className="workbench-environment" aria-label="当前运行环境">
    <div className="workbench-environment-heading"><h2>运行环境</h2>
      {onOpenSettings ? <Tooltip title="模型与运行设置"><Button type="text" size="small" icon={<SettingOutlined />} aria-label="打开运行设置" onClick={onOpenSettings} /></Tooltip> : null}
    </div>
    <div className="workbench-model">
      <ApiOutlined aria-hidden="true" />
      <span className="workbench-environment-label">模型</span>
      <span className="workbench-model-name" title={provider?.model}>{hasModel ? provider!.model : "未选择模型"}</span>
      {hasModel && !provider?.api_key_configured ? <span className="workbench-config-warning">密钥未配置</span> : null}
    </div>
    <span className={`workbench-sandbox-state is-${sandboxPhase}`}>
      <SafetyCertificateOutlined aria-hidden="true" />
      {sandboxPhase === "healthy" ? "沙箱已就绪" : sandboxPhase === "checking" ? "正在检查沙箱" : "沙箱未就绪"}
    </span>
  </section>;
}
