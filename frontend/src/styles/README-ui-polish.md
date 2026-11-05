# 样式分层

`index.css` 按固定顺序装配全局样式，顺序即层叠契约。

- `foundation-ui-polish.css`：变量、reset 和应用骨架。
- `chat-messages-ui-polish.css`、`chat-runtime-ui-polish.css`、`chat-markdown-ui-polish.css`、`chat-todo.css`：消息、Runtime Item、Markdown/数学与 Todo。
- `composer-core-ui-polish.css`、`composer-decisions.css`、`composer-completion-ui-polish.css`、`composer-files.css`：Composer、决策、补全和文件上传。输入区保留任务说明与验收摘要；工具栏用“任务环境”和“任务执行”分组，在窄空间允许换行，保持上传、模式、权限、思考、验收和发送入口可辨认。
- `pages.css`、`chat-motion.css`：页面布局与动画。
- `overrides-sidebar-ui-polish.css`、`overrides-runtime.css`、`overrides-pages.css`：按领域覆盖 Ant Design 默认样式。
- `responsive.css`、`right-panel.css`：通用响应式布局与多窗口面板。
- `workbench-ui-polish.css`：任务优先的首页布局、环境辅助区、默认折叠的 Harness 说明和任务建议。以 `.chat-page` 的 `chat` 容器查询控制布局：1040px 起启用右侧 252px 辅助栏，低于该宽度使用上下布局，699px 及以下辅助区与建议纵向排列。首页内容最大宽度 1080px；有消息后的聊天区恢复原有消息滚动与底部 Composer。不得重加大幅欢迎图或依靠浏览器宽度保留首页侧栏，分屏同样应收拢。
- `acceptance-settings-ui-polish.css`：验收配置的渐进展开、额度摘要、字段级错误、焦点轮廓和窄屏布局。正文可滚动，头部与底部操作不随正文滚走；默认展示条件和修复次数，高级工具额度按需展开。
- `acceptance-result-ui-polish.css`：验收状态、最新报告统计、用量及逐项结果。最新报告展开、历史折叠由组件的原生 `details` 承载，样式不以隐藏统计或替换颜色改变状态含义；长路径、命令及错误内容允许换行。
- `sandbox-notice-ui-polish.css`、`evaluation-workbench-ui-polish.css`：环境提示与评测工作台；放在通用组件样式后，各自包含窄屏规则。

工作台和聊天使用 16px 正文、15px 操作文字、13–14px 辅助文字、18px 区块标题与 8/12/16/24px 间距；基础字号令牌定义在 `foundation-ui-polish.css`，组件库同步由 `app/theme-ui-polish.ts` 配置。颜色使用现有主题变量，保留键盘焦点和深浅主题可读性。首页与 Composer 的领域规则各有归属，不在多个末尾覆盖块中重复修补。

任务建议和 Harness 示意使用本地 SVG，配色跟随主题，不加载外部字体或图像。图形说明能力而非执行进度；验收和评测比例条只使用真实报告数据，无报告或无分母时不得伪造完成度。

新增选择器应进入对应领域文件；不得重新建立单一超大 overrides 文件。涉及首页断点时按实际聊天容器宽度验证，不能只依据设备或窗口标签判断布局。
