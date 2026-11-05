# 聊天页面

该包实现 Composer、Turn 消息投影和同 Turn steering/rewind 交互。

- `ChatPage.tsx`：页面级组合、slash command、Turn 提交和主要视图渲染；首页以任务输入为主，环境、说明和建议作为辅助。有消息后恢复消息区与底部输入布局，当前 Turn 的验收结果展示在消息列表页脚。保留 `composerAction`、`CHAT_COMPACT_WIDTH` 的稳定导出。
- `ChatToolbar.tsx`：Chat/Trace 视图切换和主 Thread/Subagent Thread 选择工具栏。
- `WorkbenchWelcome-ui-polish.tsx`：任务起始页标题；`heroOnly` 只展示简短标题与目标说明。`WorkbenchGuide` 将 Harness 工作方式放入默认折叠的说明区，流程只解释能力，不表示任务已执行或验收通过。`WorkbenchStarters` 提供键盘可操作的任务建议，点击后插入草稿、聚焦编辑器并显示填入反馈，不自动创建会话或发送。
- `WorkbenchEnvironment.tsx`：展示当前模型配置与真实沙箱健康状态，不探测模型 API、不根据配置声称已连通。
- `WorkbenchIllustrations-ui-polish.tsx`：任务建议的目录结构、代码编辑、检查定位示意，以及 Harness 执行/验收关系图。SVG 跟随主题、从可访问树隐藏，不作为运行状态或交互入口。
- `SandboxNotice.tsx`：环境未就绪时显示可操作的中文提示，原始脱敏根因保留在技术详情；只复用健康状态与检查回调，不改变自动恢复和禁止发送策略。
- `AcceptanceSettings.tsx`：默认展示验收条件与最多修复次数；工具调用总上限、预留验收次数放入默认折叠的高级限制，折叠后仍显示当前额度摘要。草稿由组件本地状态保存，折叠不丢设置；编辑保留已有检查 ID、命令超时、模型轮次及未显式配置的默认字段。新增条件自动聚焦，校验失败关联具体字段的 `aria-invalid` 与错误描述，并聚焦错误字段；高级限制错误会自动展开。弹窗正文独立滚动，应用与取消操作固定在底部。工具次数不代表费用上限。
- `AcceptanceResult-ui-polish.tsx`：按“状态与最新报告通过数 → 用量 → 最新检查 → 历史检查”展示结果。通过数只匹配当前契约中的检查 ID，不累加历史轮次或契约外结果；缺失结果显示未执行，没有报告时显示待验收。最新报告默认展开，历史默认折叠；`budget_exhausted` 显示为“调用额度耗尽”。保留报告下载、Trace 入口以及原有加载与运行中轮询逻辑。
- `useResponsiveChatLayout.ts`：通过 Ant Design breakpoint 和容器 `ResizeObserver` 统一计算移动端/紧凑布局。
- `useChatScroll.ts`：维护 24px 底部阈值、对话切换时的底部锚定、内容尺寸变化跟随和手动回到底部操作。
- `useChatCommands.ts`：封装 slash command 补全，以及 `/trace`、`/help`、`/skills`、`/compact` 的控制流和 compact pending 清理。
- `useQueuedMessageFlow.ts`：封装 queue create/update/delete、运行中 steering、终态自动 FIFO flush、delivery acknowledgement 刷新和编辑回填。
- `contracts.ts`：`ChatPageProps`、`ChatRunRequest`、`PendingUpload` 和 `composerAction`。
- `useRuntimeControls.ts`：running Turn 的 mode/permission/reasoning 热更新。
- `useComposerFiles.ts`：`@` 引用搜索、上传、重试、删除与会话切换清理。
- `useMessageEditing.ts`：用户消息编辑、rewind、fork 和版本切换。
- `ChatMessageList.tsx`、`messageParts.tsx`：消息列表、Ant Design X 用户 `Bubble`、Assistant Runtime Item 与操作按钮；保留现有虚拟滚动、编辑、重试和版本切换。
- `Composer.tsx`、`FileMentionEditor.tsx`、`QueuedMessageList.tsx`：输入、补全、上传和 FIFO 队列 UI。首页 Composer 显示任务说明、验收摘要与目标输入提示；工具栏分为“任务环境”（上传、运行模式、权限）和“任务执行”（已知上下文用量、思考等级、验收、发送或运行操作）两组，紧凑布局保留文字与可访问名称，不以占位用量冒充真实数据。
- `ConversationTimeline.tsx`、`TimelineTicks.module.css`：长对话导航。
- `todoPanel.tsx`：从最新 todo Item 投影任务列表。

文件与命令补全菜单在首页优先向下、聊天中优先向上展开，根据视口和滚动容器的可用空间限高，必要时切换方向。键盘切换候选只滚动菜单内部，避免页面或聊天位置跳动。

首页布局由 `styles/workbench-ui-polish.css` 按 `.chat-page` 容器宽度决定，而非只看浏览器窗口。容器达到 1040px 时，任务区与右侧 252px 的环境/说明辅助栏并排；更窄时改为上下布局，容器不超过 699px 时辅助区和任务建议都纵向排列。该规则也适用于分屏会话，不得通过全局视口断点强制保留侧栏。

API/SSE 由 `api/` 和 `app/runController` 提供；本包必须保持 pause/steer、rewind 和 `Message[][]` 协议语义。Ant Design X 只负责展示，按 `@ant-design/x/es/<component>` 按需导入；不接管模型请求、消息列表滚动或运行状态。Composer 保留 Lexical 编辑器，避免替换输入框时丢失文件节点、IME、补全与队列行为。

HTTP `detail`、SSE terminal error 与 Turn error Item 的消息直接展示 backend 提供的脱敏根因文本；Chat、Trace、Compact、运行配置和 Agent Thread 交互不得再附加业务失败前缀。

`retry` Item 在所有 Runtime 展示级别可见：running 时用 polite live status 显示“网络异常，正在重试（n/N）”及 backend 根因消息，结算后保留紧凑的“网络请求已重试”历史；Trace 将其归类为 `Network Retry`，不得按 Tool 展示。
