import { Button, Dropdown, Progress, Select, Tooltip } from "antd";
import {
  ArrowUpOutlined,
  BulbOutlined,
  PaperClipOutlined,
  PauseCircleTwoTone,
  PlayCircleTwoTone,
  RobotOutlined,
  SafetyOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject, type ReactNode } from "react";
import type { ChatMode, PermissionMode, ReasoningEffort, TodoItem } from "../../types";
import IconAction from "../../components/IconAction";
import { useButtonTooltips } from "../../components/ButtonTooltipContext";
import type { FileCandidate } from "../../commands/fileCompletion";
import { sessionFileContentUrl } from "../../api/projects/files";
import { SessionTodoPanel } from "./todoPanel";
import FileMentionEditor, { type FileMentionChange, type FileMentionEditorHandle } from "./FileMentionEditor";
import QueuedMessageList from "./QueuedMessageList";
import type { QueuedMessage } from "../../app/types";

const REASONING_LABELS: Record<ReasoningEffort, string> = { low: "低", medium: "中", high: "高", xhigh: "超高", max: "最大" };
const MODE_LABELS: Record<ChatMode, string> = { agent: "Agent", plan: "Plan" };
const PERMISSION_LABELS: Record<PermissionMode, string> = {
  read_only: "只读",
  workspace_write: "工作区读写",
  full_access: "完全访问",
};
export type SettingsSelectKey = "mode" | "permission" | "reasoning";
export type ComposerActionMode = "send" | "pause" | "resume" | "steer";

export interface ComposerProps {
  acceptanceControl?: ReactNode;
  acceptanceSummary?: string;
  input: string;
  busy: boolean;
  actionMode?: ComposerActionMode;
  submitDisabled?: boolean;
  startMode?: boolean;
  compact: boolean;
  filteredCommands: Array<{ name: string; label: string; description: string }>;
  commandMenuVisible: boolean;
  activeCommandIndex: number;
  mode: ChatMode;
  permissionMode: PermissionMode;
  reasoningEffort: ReasoningEffort;
  modePending?: boolean;
  permissionPending?: boolean;
  reasoningPending?: boolean;
  todos: TodoItem[] | null;
  home?: boolean;
  todoClosable?: boolean;
  onTodoClose?: () => void;
  usagePercent?: number;
  usageTotalTokens?: number | null;
  usageContextLength?: number;
  openSettingsSelect: SettingsSelectKey | null;
  editorRef: RefObject<FileMentionEditorHandle>;
  sessionId?: string;
  onEditorChange: (change: FileMentionChange) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onComplete: (index?: number) => void;
  onCommandExecute: (index?: number) => void;
  onActiveCommandChange: (index: number) => void;
  onModeChange: (mode: ChatMode) => void;
  onPermissionChange: (mode: PermissionMode) => void;
  onReasoningChange: (effort: ReasoningEffort) => void;
  onSettingsSelectChange: (key: SettingsSelectKey | null) => void;
  onStop: () => void;
  onSend: () => void;
  disabled?: boolean;
  queueSendDisabled?: boolean;
  inputDisabled?: boolean;
  disabledReason?: string;
  // File references: completion menu + inline editor nodes.
  fileCandidates: FileCandidate[];
  fileMenuVisible: boolean;
  activeFileIndex: number;
  fileMenuQuery: string;
  onFileComplete: (index?: number) => void;
  onActiveFileChange: (index: number) => void;
  onPickFiles: (files: FileList | File[]) => void;
  uploadsDisabled?: boolean;
  pendingUploads: Array<{
    uid: string;
    name: string;
    isImage: boolean;
    status: "uploading" | "done" | "error";
    percent: number;
    path?: string;
    displayPath?: string;
    error?: string;
  }>;
  onRemoveUpload: (index: number) => void;
  onRetryUpload: (index: number) => void;
  onUploadPreview: (index: number) => void;
  uploadsUploading?: boolean;
  queuedMessages?: QueuedMessage[];
  onQueueSend?: (item: QueuedMessage) => void;
  onQueueEdit?: (item: QueuedMessage) => void;
  onQueueDelete?: (item: QueuedMessage) => void;
}

export default function Composer(props: ComposerProps) {
  const showButtonTooltips = useButtonTooltips();
  const composerRef = useRef<HTMLDivElement | null>(null);
  const fileMenuRef = useRef<HTMLDivElement | null>(null);
  const commandMenuRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [completionLayout, setCompletionLayout] = useState({ below: Boolean(props.home), height: 280 });
  const actionMode = props.actionMode ?? (props.busy ? "pause" : "send");

  useLayoutEffect(() => {
    const composer = composerRef.current;
    if (!composer || (!props.fileMenuVisible && !props.commandMenuVisible)) return;
    const clippingParents: HTMLElement[] = [];
    for (let parent = composer.parentElement; parent; parent = parent.parentElement) {
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) clippingParents.push(parent);
    }
    const measure = () => {
      const viewport = window.visualViewport;
      let top = viewport?.offsetTop ?? 0;
      let bottom = top + (viewport?.height ?? window.innerHeight);
      for (const parent of clippingParents) {
        const bounds = parent.getBoundingClientRect();
        top = Math.max(top, bounds.top + parent.clientTop);
        bottom = Math.min(bottom, bounds.top + parent.clientTop + parent.clientHeight);
      }
      const anchor = composer.getBoundingClientRect();
      const above = Math.max(0, anchor.top - top - 8);
      const below = Math.max(0, bottom - anchor.bottom - 8);
      // Home starts near the top; ongoing conversations keep their upward menu.
      // Flip only when the preferred side cannot fit even a few candidates.
      const openBelow = props.home ? below >= 120 || below >= above : above < 120 && below > above;
      const height = Math.min(280, Math.floor(openBelow ? below : above));
      setCompletionLayout((previous) => previous.below === openBelow && previous.height === height
        ? previous : { below: openBelow, height });
    };
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : undefined;
    observer?.observe(composer);
    clippingParents.forEach((parent) => observer?.observe(parent));
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    window.visualViewport?.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("scroll", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      window.visualViewport?.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("scroll", measure);
    };
  }, [props.home, props.fileMenuVisible, props.commandMenuVisible]);

  useLayoutEffect(() => {
    const menus = [[fileMenuRef.current, props.activeFileIndex], [commandMenuRef.current, props.activeCommandIndex]] as const;
    for (const [menu, index] of menus) {
      const selected = menu?.querySelectorAll<HTMLElement>(".command-item")[index];
      if (!menu || !selected) continue;
      // Scroll only the menu, unlike scrollIntoView which can move the chat/page.
      const top = selected.offsetTop;
      const bottom = top + selected.offsetHeight;
      if (top < menu.scrollTop) menu.scrollTop = top;
      else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight;
    }
  }, [props.activeFileIndex, props.activeCommandIndex, props.fileMenuVisible, props.commandMenuVisible,
    props.fileCandidates, props.filteredCommands, completionLayout.height]);

  function openFilePicker() {
    if (props.disabled || props.uploadsDisabled) return;
    fileInputRef.current?.click();
  }

  function handleFilesChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (files && files.length > 0) props.onPickFiles(files);
    event.target.value = "";
  }

  function handlePaste(event: globalThis.ClipboardEvent) {
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length > 0 && !props.disabled && !props.uploadsDisabled) {
      event.preventDefault();
      props.onPickFiles(files);
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    setDragOver(false);
    const files = event.dataTransfer?.files;
    if (files && files.length > 0 && !props.disabled && !props.uploadsDisabled) {
      event.preventDefault();
      props.onPickFiles(files);
    }
  }

  function handleComposerKeyDownCapture(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target instanceof HTMLElement && event.target.matches(".file-mention-editor")) {
      props.onKeyDown(event);
      if (event.defaultPrevented) event.stopPropagation();
    }
  }

  const usageDescription = props.usageTotalTokens == null ? "" : `上下文用量：${props.usageTotalTokens.toLocaleString()}${props.usageContextLength ? ` / ${props.usageContextLength.toLocaleString()}` : ""} tokens`;
  const usageIndicator = props.usageTotalTokens == null ? null : (
    <Tooltip title={usageDescription}>
      <span className="composer-usage" role="img" aria-label={usageDescription} tabIndex={0}>
        <Progress aria-hidden="true" type="circle" size={32} percent={Math.max(0, Math.min(100, props.usagePercent ?? 0))}
          format={() => props.usageTotalTokens! >= 1000 ? `${(props.usageTotalTokens! / 1000).toFixed(1)}k` : String(props.usageTotalTokens)} />
      </span>
    </Tooltip>
  );
  const settingsControls = (
    <div className="composer-settings-controls">
      <Select virtual={false} variant="borderless" className="mode-picker" placement="topLeft" open={props.openSettingsSelect === "mode"} aria-label="运行模式" loading={props.modePending} disabled={props.disabled || props.modePending} value={props.mode} options={[{ value: "agent", label: <><RobotOutlined /> Agent</> }, { value: "plan", label: <><BulbOutlined /> Plan</> }]} onChange={props.onModeChange} onOpenChange={(open) => props.onSettingsSelectChange(open ? "mode" : null)} />
      <Select virtual={false} variant="borderless" className="composer-picker permission-picker" placement="topLeft" open={props.openSettingsSelect === "permission"} aria-label="权限模式" loading={props.permissionPending} disabled={props.disabled || props.permissionPending} value={props.permissionMode} options={[{ value: "read_only", label: "只读" }, { value: "workspace_write", label: "工作区读写" }, { value: "full_access", label: "完全访问" }]} onChange={props.onPermissionChange} onOpenChange={(open) => props.onSettingsSelectChange(open ? "permission" : null)} />
    </div>
  );
  const compactSettingsControls = (
    <div className="composer-compact-settings">
      <Tooltip title={showButtonTooltips ? `运行模式：${MODE_LABELS[props.mode]}` : undefined}>
        <Dropdown
          trigger={["click"]}
          placement="topLeft"
          open={props.openSettingsSelect === "mode"}
          onOpenChange={(open) => props.onSettingsSelectChange(open ? "mode" : null)}
          menu={{
            selectable: true,
            selectedKeys: [props.mode],
            items: (Object.keys(MODE_LABELS) as ChatMode[]).map((value) => ({
              key: value,
              label: MODE_LABELS[value],
              onClick: () => props.onModeChange(value),
            })),
          }}
        >
          <Button type="text" size="small" className="composer-compact-setting" icon={<RobotOutlined />} aria-label={`运行模式：${MODE_LABELS[props.mode]}`} loading={props.modePending} disabled={props.disabled || props.modePending}>{MODE_LABELS[props.mode]}</Button>
        </Dropdown>
      </Tooltip>
      <Tooltip title={showButtonTooltips ? `权限模式：${PERMISSION_LABELS[props.permissionMode]}` : undefined}>
        <Dropdown
          trigger={["click"]}
          placement="topLeft"
          open={props.openSettingsSelect === "permission"}
          onOpenChange={(open) => props.onSettingsSelectChange(open ? "permission" : null)}
          menu={{
            selectable: true,
            selectedKeys: [props.permissionMode],
            items: (Object.keys(PERMISSION_LABELS) as PermissionMode[]).map((value) => ({
              key: value,
              label: PERMISSION_LABELS[value],
              onClick: () => props.onPermissionChange(value),
            })),
          }}
        >
          <Button type="text" size="small" className="composer-compact-setting" icon={<SafetyOutlined />} aria-label={`权限模式：${PERMISSION_LABELS[props.permissionMode]}`} loading={props.permissionPending} disabled={props.disabled || props.permissionPending}>{PERMISSION_LABELS[props.permissionMode]}</Button>
        </Dropdown>
      </Tooltip>
    </div>
  );
  const reasoningControl = props.compact ? (
    <Tooltip title={showButtonTooltips ? `思考等级：${REASONING_LABELS[props.reasoningEffort]}` : undefined}>
      <Dropdown
        trigger={["click"]}
        placement="topLeft"
        open={props.openSettingsSelect === "reasoning"}
        onOpenChange={(open) => props.onSettingsSelectChange(open ? "reasoning" : null)}
        menu={{
          selectable: true,
          selectedKeys: [props.reasoningEffort],
          items: (Object.keys(REASONING_LABELS) as ReasoningEffort[]).map((value) => ({
            key: value,
            label: `${REASONING_LABELS[value]}（${value}）`,
            onClick: () => props.onReasoningChange(value),
          })),
        }}
      >
        <Button type="text" size="small" className="composer-compact-setting" icon={<BulbOutlined />} aria-label={`思考等级：${REASONING_LABELS[props.reasoningEffort]}`} loading={props.reasoningPending} disabled={props.disabled || props.reasoningPending}>思考{REASONING_LABELS[props.reasoningEffort]}</Button>
      </Dropdown>
    </Tooltip>
  ) : (
    <Select virtual={false} variant="borderless" className="composer-picker reasoning-picker" placement="topLeft" open={props.openSettingsSelect === "reasoning"} aria-label="思考等级" loading={props.reasoningPending} disabled={props.disabled || props.reasoningPending} value={props.reasoningEffort} options={(Object.keys(REASONING_LABELS) as ReasoningEffort[]).map((level) => ({ value: level, label: `${level}` }))} onChange={props.onReasoningChange} onOpenChange={(open) => props.onSettingsSelectChange(open ? "reasoning" : null)} />
  );
  return (
    <div
      ref={composerRef}
      className={`composer composer-reveal-shell${props.todos && props.todos.length > 0 ? " has-todo" : ""}`}
      data-composer-seat
    >
      {props.fileMenuVisible && (
        <div ref={fileMenuRef} className="command-menu file-menu" role="listbox" aria-label="文件补全"
          data-placement={completionLayout.below ? "below" : "above"} style={{ maxHeight: completionLayout.height }}>
          {props.fileCandidates.length === 0 ? (
            <div className="command-menu-empty">没有匹配的文件（“{props.fileMenuQuery}”）</div>
          ) : (
            props.fileCandidates.map((candidate, index) => (
              <button
                key={`${candidate.reference.source}:${candidate.reference.path}`}
                type="button"
                className={`command-item file-item${index === props.activeFileIndex ? " selected" : ""}`}
                onMouseEnter={() => props.onActiveFileChange(index)}
                onClick={() => props.onFileComplete(index)}
              >
                <span className="file-item-name">{candidate.file.name}</span>
                <span className="file-item-path" title={candidate.reference.display_path}>{candidate.reference.display_path}</span>
                <span className={`file-source-badge ${candidate.reference.source}`}>{candidate.sourceLabel}</span>
              </button>
            ))
          )}
        </div>
      )}
      {props.commandMenuVisible && <div ref={commandMenuRef} className="command-menu" data-placement={completionLayout.below ? "below" : "above"} style={{ maxHeight: completionLayout.height }}>{props.filteredCommands.map((command, index) => <button key={command.name} type="button" className={`command-item${index === props.activeCommandIndex ? " selected" : ""}`} onMouseEnter={() => props.onActiveCommandChange(index)} onClick={() => props.onCommandExecute(index)}><span className="command-name">{command.name}</span><span className="command-desc">{command.label} · {command.description}</span></button>)}</div>}
      <div className="composer-box-anchor">
        <QueuedMessageList
          items={props.queuedMessages ?? []}
          disabled={props.disabled}
          sendDisabled={props.queueSendDisabled}
          onSend={(item) => props.onQueueSend?.(item)}
          onEdit={(item) => props.onQueueEdit?.(item)}
          onDelete={(item) => props.onQueueDelete?.(item)}
        />
        {props.pendingUploads.length > 0 ? (
          <div className="composer-uploads composer-reveal-item" data-reveal-index="1" aria-label="上传进度">
            {props.pendingUploads.map((upload, index) => (
              <span key={upload.uid} className={`composer-upload status-${upload.status}`}>
                {upload.isImage && upload.status === "done" && upload.path && props.sessionId ? (
                  <img className="composer-upload-thumb" src={sessionFileContentUrl(props.sessionId, "upload", upload.path)} alt={upload.name} onClick={() => props.onUploadPreview(index)} />
                ) : <span className="composer-upload-icon">📄</span>}
                <span className="composer-upload-name" title={upload.displayPath ?? upload.name}>{upload.displayPath ?? upload.name}</span>
                {upload.status === "uploading" ? <Progress size="small" percent={upload.percent} showInfo={false} className="composer-upload-progress" /> : null}
                {upload.status === "error" ? <span className="composer-upload-error" title={upload.error}>上传失败</span> : null}
                {upload.status === "error" ? <Button type="text" size="small" onClick={() => props.onRetryUpload(index)}>重试</Button> : null}
                <Button type="text" size="small" className="composer-upload-remove" aria-label={`移除 ${upload.name}`} onClick={() => props.onRemoveUpload(index)}>×</Button>
              </span>
            ))}
          </div>
        ) : null}
        {props.todos && props.todos.length > 0 ? (
          <div className="composer-todo-anchor composer-reveal-item" data-reveal-index="1">
            <SessionTodoPanel
              key={props.todoClosable ? "closable" : "active"}
              todos={props.todos}
              busy={props.busy && !props.todoClosable}
              closable={props.todoClosable}
              onClose={props.onTodoClose}
            />
          </div>
        ) : null}
        <div className={`composer-box composer-reveal-item${dragOver ? " is-dragging" : ""}`} data-reveal-index="2" onKeyDownCapture={handleComposerKeyDownCapture} onDragOver={(event) => { if (!props.disabled) { event.preventDefault(); setDragOver(true); } }} onDragLeave={() => setDragOver(false)} onDrop={handleDrop}>
          {props.home ? <div className="composer-task-heading"><strong>任务说明</strong><span>{props.acceptanceSummary}</span></div> : null}
          <input ref={fileInputRef} type="file" multiple hidden aria-hidden="true" onChange={handleFilesChange} />
          <FileMentionEditor
            ref={props.editorRef}
            disabled={props.disabled || props.inputDisabled}
            placeholder={props.disabledReason || (props.home ? "描述任务目标、相关文件与完成标准…" : "继续对话，或用 / 执行命令…")}
            onChange={props.onEditorChange}
            onPasteFiles={handlePaste}
          />
          <div className="composer-toolbar composer-reveal-item" data-reveal-index="4">
            <div className="composer-toolbar-group composer-toolbar-main" role="group" aria-label="任务环境">
              <IconAction className="file-upload-trigger" label="上传文件" icon={<PaperClipOutlined />} disabled={props.disabled || props.uploadsDisabled} onClick={openFilePicker} />
              {props.compact ? compactSettingsControls : settingsControls}
            </div>
            <div className="composer-toolbar-group composer-toolbar-submit" role="group" aria-label="任务执行">
              {usageIndicator}
              {reasoningControl}
              {props.acceptanceControl}
              <button
                className={actionMode === "pause" ? "send-btn stop" : "send-btn"}
                type="button"
                aria-label={actionMode === "pause" ? "暂停" : actionMode === "steer" ? "追加指令" : actionMode === "resume" ? "继续" : props.startMode ? "开始" : "发送"}
                onClick={actionMode === "pause" || actionMode === "steer" ? props.onStop : props.onSend}
                disabled={props.submitDisabled ?? (props.disabled || props.uploadsUploading || (!props.startMode && !props.input.trim() && props.pendingUploads.every((upload) => upload.status !== "done")))}
              >
                {actionMode === "pause" ? <PauseCircleTwoTone aria-hidden="true" />
                  : actionMode === "steer" ? <SendOutlined aria-hidden="true" />
                    : actionMode === "resume" ? <PlayCircleTwoTone aria-hidden="true" /> : <ArrowUpOutlined aria-hidden="true" />}
              </button>
            </div>
          </div>
        </div>
      </div>
      <p className="composer-hint">Enter 发送 · Shift + Enter 换行 · @ 引用文件 · / 使用命令</p>
    </div>
  );
}
