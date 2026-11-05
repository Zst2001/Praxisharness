import { useState } from "react";
import { Alert, Button, Input, Modal, Space } from "antd";
import { saveProjectExperience, type RepairSource } from "../../api/memory";
import { ErrorDisplay } from "../../components/ErrorDisplay";

export default function SaveProjectExperience({ source }: { source: RepairSource }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | string | null>(null);
  const [situation, setSituation] = useState("");
  const [recommendation, setRecommendation] = useState("");

  async function save() {
    if (saving || !situation.trim() || !recommendation.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await saveProjectExperience(source, situation.trim(), recommendation.trim());
      setSaved(true);
      setOpen(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure : String(failure));
    } finally {
      setSaving(false);
    }
  }

  if (saved) return <span role="status">已保存为项目经验</span>;
  return <>
    <Button size="small" onClick={() => { setError(null); setOpen(true); }}>保存为项目经验</Button>
    <Modal title="保存为项目经验" open={open} okText="确认保存" cancelText="取消" confirmLoading={saving}
      okButtonProps={{ disabled: saving || !situation.trim() || !recommendation.trim() }}
      cancelButtonProps={{ disabled: saving }} closable={!saving}
      onCancel={() => { if (!saving) setOpen(false); }} onOk={() => void save()}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        <p>条件 {source.check_id} · 第 {source.failed_attempt} 轮失败 → 第 {source.failed_attempt + 1} 轮通过。保存后，本项目的新对话可以检索这条经验。</p>
        <label>适用情形<Input maxLength={300} value={situation}
          onChange={(event) => setSituation(event.target.value)} placeholder="什么情况下适用？" /></label>
        <label>建议做法<Input.TextArea rows={4} maxLength={2000} value={recommendation}
          onChange={(event) => setRecommendation(event.target.value)} placeholder="下次可以尝试什么，怎样验证？" /></label>
        {error && <Alert type="error" showIcon title={<ErrorDisplay error={error} />} />}
      </Space>
    </Modal>
  </>;
}
