# Harness 第一阶段验证记录

日期：2026-11-03。环境：Windows、Python 3.13.13，依赖按仓库 `uv.lock` 和前端 lockfile 安装。本目录没有 `.git`，无法生成 Git diff 或建立修改前的完整测试基线；本轮没有提交或推送。

这是第一阶段的历史记录。后续失败排查、三组实验及产品完善结果见 [第二阶段验证](harness-stage2-validation.md)。

## 实现与验证范围

- 固定合同：文件存在、字面文本、完整 JSON 内容、命令退出码；schema、唯一 ID、路径边界和文件读取上限。
- 完成门控：验收失败反馈、有限修复、模型轮数和工具预算；手动完成请求不能认证未通过的验收。
- 命令复用现有 ToolBatch / ToolStep、权限、审批和沙箱机制；测试覆盖成功、非零退出、拒绝、沙箱不可用、超时和预算耗尽后停止命令。
- Turn API：本地模型 HTTP/SSE → Runtime → 真实文件工具 → SQLite 状态与验收证据；非法输入和 Origin 被拒绝，恢复任务不能替换已保存的合同。
- 浏览器：桌面 1365×900、手机 390×844；设置条件、应用、重开编辑、缩放后的状态保留和页面宽度检查。配置界面可在 Broker 不可用时使用，执行门控仍保持原策略。
- 对照评测：固定任务、独立初始工作区、相同工具和预算、独立产物评分，记录模型、套件哈希和逐项证据。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| 新增后端验收与 HTTP/API 评测测试 | 27 项通过 |
| 后端相关回归，含工具拒绝、预算、上下文、Todo、恢复、配置、Provider transport、工具并发和架构边界 | 最终版本重跑 141 项通过 |
| 前端全量测试 | 68 个文件、554 项通过 |
| 最后一次前端相关回归，覆盖设置组件、AgentShell 和 ChatPage | 89 项通过 |
| 前端类型检查、生产构建 | 通过；保留现有大 bundle 提示 |
| Ruff lint | 全仓通过 |
| 本轮变更 Python 文件格式 | 21 个文件通过 |
| 浏览器设置交互 | 桌面和手机通过，无横向溢出 |

全部模型协议集成验证使用独立 loopback HTTP 服务，没有调用付费模型。真实命令测试使用明确的测试启动器；涉及进程树取消时沿用现有 ProcessGroup 的平台终止器。未安装、修复或重新配置用户的 Sandbox Broker，本记录不能证明 Broker 隔离能力。

## 对照流程演示

执行 `python -m benchmarks.harness --repeat 3`，共 18 次执行，每组 9 次。脚本规划器的 baseline 9 次均未满足产物规则；repair 9 次均在一次反馈修复后通过。报告含 `mode=scripted_demo`、`capability_score=false`，没有真实模型 Token 使用量。

这些结果用于证明反馈、修复、独立评分和报告流程工作，不能作为简历中的模型完成率或提升比例。后续必须用固定真实模型、扩充任务与重复实验重新测量。

## 全量后端未通过项

全量运行结束时为 **1655 通过、78 跳过、14 失败**。这不是全量绿色版本。失败分布：

| 类别 | 数量 | 现象 |
| --- | ---: | --- |
| 旧 Benchmark Trace 断言 | 1 | 空 Trace context 返回空结构，而断言要求 `None` |
| 协作模式切换断言 | 4 | assistant / developer 消息顺序与预期不一致 |
| 旧 Provider 迁移预期 | 2 | `deepseek` 与 `default` 名称预期不一致 |
| MCP HTTP 集成 | 5 | 本地 HTTP 初始化 / 鉴权测试超时或返回 CancelledError |
| Windows 子进程错误报告、安装锁 | 2 | Broker repair 状态及安装锁子进程测试未通过 |

单独运行旧 Benchmark 用例也能复现其 Trace 断言失败。由于没有修改前 checkout，不能仅凭本轮结果认定全部失败都早于本轮；本轮没有为消除这些失败而增加旧 Provider 迁移或改变 MCP / Broker 行为。

全仓格式检查另发现 5 个未在本轮编辑的测试文件需要格式化：`test_plan_questions.py`、`test_provider_transport.py`、`test_resume.py`、`test_runtime_invariants.py`、`test_stream_attempt_rollback.py`。保留其原始内容。本轮另修正 `DecisionCard.test.tsx` 的两处不支持的 `exact` 参数，保持按钮精确名称匹配，使前端类型检查通过。

运行方法与边界详见 [Harness 使用说明](harness.md)。
