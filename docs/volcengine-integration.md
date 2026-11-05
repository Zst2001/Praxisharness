# 火山引擎 DeepSeek 接入与真实试跑

验证日期：2026-11-04。已在当前 Windows 用户的本地 Praxis 安装中保存并激活 `volcengine-deepseek`，通过现有 Chat Completions Provider 调用火山方舟，无需新增独立模型适配层。

本文保留首轮接入与试跑记录。后续累计用量修正、代码评测集、6 次真实复测和最新预算见 [后续实现记录](harness-next-validation.md)；下方金额为首轮完成时的快照。

## 已启用的配置

| 配置 | 值 |
| --- | --- |
| Provider | `volcengine-deepseek`（当前默认） |
| 模型 | `deepseek-v4-1-flash-261017` |
| 协议 | `chat_completions` |
| Base URL | `https://ark.cn-beijing.volces.com/api/v3` |
| 日常输出上限 | 8192 tokens |
| 日常本地上下文预算 | 131072 tokens |
| temperature | 0 |

依据核对时的[官方发布公告](https://docs.volcengine.com/docs/ark/model-release-announcement?lang=zh)选择最新的 DeepSeek-V4.1-Flash 修订，并固定版本 ID，避免浮动别名影响实验复现。日常配置与下方试跑的上下文、输出限制不同。

凭据通过现有 OS credential vault 安装密钥加密，密文保存在 `~/.praxis/runtime/state.db`。`~/.praxis/config.toml`、仓库源码与公开设置接口均不保存或返回明文 API Key。未将 Key ID 写入配置。实际 `/api/settings` 已返回已配置、已激活状态；刷新设置页面即可查看该 Provider。

这份配置属于当前用户的本地安装，复制仓库到另一台电脑时需要在设置中配置该设备的凭据。

## 真实验证结果

先使用真实 Provider、LLMPlanner、文件工具和 Runtime 验收执行 smoke：创建 `result.json`，内容精确等于 `{"ok": true}`。结果完成并通过验收，共 3 次模型请求、2 次工具调用；响应模型版本与配置一致。

随后使用 `normalize-config`、`write-report`、`release-version` 三个文件任务，各运行 baseline、verify、repair 三组，共 9 次 trial。关闭 Skills、用户记忆和深度思考；各组使用相同任务和文件工具，独立临时工作区，退出后独立评分。模型决策上限 6、工具上限 12、最多修复 2 次；输出上限 2048，本地上下文预算 32768，单次请求超时 60 秒，常规 transport 重试上限 0。底层发生额外 HTTP 尝试时也必须先预留费用。

| 组别 | 独立评分通过 | 其他结果 |
| --- | --- | --- |
| baseline | 2 / 3 | 1 次模型请求超时 |
| verify | 3 / 3 | 无 |
| repair | 3 / 3 | 无 |

这是接入与实验流程的真实小样本记录，不能据此声称 Harness 提升了成功率。baseline 的差异包含服务超时；也不能用这组结果证明反馈修复带来收益。报告继续标记 `capability_score=false`。

当前 Windows Broker 服务未运行，命令与代码任务尚未完成真实隔离验证。本轮明确只向模型提供文件工具；没有绕过 Broker 在宿主执行命令。应用中的命令执行仍需先恢复 Broker。

## ¥10 预算如何执行

新增 `benchmarks.spending`，在每一次实际 HTTP 发送前，以 SQLite 事务预留该固定模型的最坏情况费用。预留按官方最大输入窗口 1048576 tokens 加本次输出上限计算，不依赖本地分词估算。成功取得有效用量后才回收差额；请求超时、缺失用量或进程退出，保留全额预留。并发调用共享同一本账本，重启后继续累计；更换已有账本的预算或价格策略会被拒绝。

采用核对时[官方常规高峰价格](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh&redirect=1)：输入 ¥2 / 百万 tokens、输出 ¥8 / 百万 tokens；缓存按全价计入，思考用量保守追加。仅允许固定地址、固定模型、文本消息和本地函数工具，限制输出 tokens、消息数量和请求体大小。

smoke 与 pilot 共用同一轮 ¥10 账本，结果如下：

| 项目 | 金额 / 数量 |
| --- | --- |
| 实际 HTTP 尝试 | 42 |
| 有已知用量的保守费用估算 | ¥0.436356 |
| 1 次未知结果的全额预留 | ¥2.113536 |
| 估算加预留 | **¥2.549892** |
| 本轮限制 | ¥10 |

这些金额是本地估算和预算占用，未核对账户账单，不能当成实际扣费。此保护只适用于本入口的试跑；普通聊天和其他程序的 API 调用不使用这本账本，平台实际计费以账户账单为准。

## 入口与产物

当前已完成本轮付费试跑，不需要再次运行。后续经授权继续同一轮时，在仓库根目录使用新的输出名称并保留默认共享账本：

```powershell
uv run python -m benchmarks.live --smoke --budget-cny 10 --output benchmarks/output/volcengine-smoke-next.json
uv run python -m benchmarks.live --budget-cny 10 --output benchmarks/output/volcengine-pilot-next.json
```

这是付费入口，每次执行会新发起请求，不自动恢复已完成 trial。禁止为了重试而删除账本；默认账本是 `benchmarks/output/volcengine-live-budget.db`，不会因为输出文件名改变而清零。输出已存在时拒绝覆盖。自动化测试使用本地模拟，不调用付费服务。

本轮文件均位于本地忽略目录：

- [smoke JSON](../benchmarks/output/volcengine-smoke.json)
- [完整 pilot JSON](../benchmarks/output/volcengine-pilot.json)
- [pilot Markdown](../benchmarks/output/volcengine-pilot.md)
- [pilot JSONL](../benchmarks/output/volcengine-pilot.trials.jsonl)
- [实际执行版本源码归档](../benchmarks/output/volcengine-pilot-source-0e7fe9aee8bc.zip)

pilot 实验 ID 为 `ebdf0f76d8a64cdfa3f3719af442aae4`，源码 SHA-256 为 `0e7fe9aee8bc9f99a8820d0e8c3d78219c6e6b1a23f2b49201cc4e562e737de8`。归档包含逐文件哈希清单，已逐项验证与实验指纹一致；没有把后续代码变更冒充为当时的执行版本。

首次 pilot 的 JSONL 从已完成 JSON 导出，元数据明确记录 `exported_from_completed_report=true`，没有为导出格式重跑付费调用。后续入口已补充逐 trial 刷新 JSONL，意外中断时保留已完成结果。

收尾验证：预算、原始用量归一化、并发预留、重启续账、发送前拦截及中断保留记录共 14 项测试通过，相关 Harness 回归 13 项通过；Ruff 检查和 550 个 Python 文件的格式检查通过。真实本地 `/api/settings` 返回 200 且不暴露凭据；968 个源码、文档及报告文件的明文密钥扫描通过。本轮未修改前端或重跑前端验证。
