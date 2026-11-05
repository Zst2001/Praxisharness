# 命令完整性与预算边界验收

日期：2026-11-04（Asia/Shanghai）。本阶段实现 `cmd` 多行命令边界、验收额度预留、预算边界的确定性收尾，以及同初始反馈下的预算策略对照。实验前端详情与运行中命令的崩溃恢复属于后续需求。

## 命令执行契约

前阶段真实轨迹出现多条多行 `python -c` 命令：退出码为 0，却缺少后续预期输出。[真实隔离诊断](../benchmarks/output/command-multiline-reproducer.json)使用诊断进程内的旧命令行构造方式，复现首行 `begin` 被执行、后续异常未执行、仍返回 0。它只复现旧行为，没有在产品增加兼容分支。最终公开与独立评分按脚本文件执行，因此保留原评分。

当前默认终端为 `cmd`。工具层在启动进程前拒绝包含 CR 或 LF 的 `cmd` 命令，返回明确错误，提示使用 `file_operation` 将代码写入工作区脚本，再以单行命令执行文件。工具 schema 同步说明该限制。没有把换行机械替换成分号或 `&&`，也没有把该限制扩展到其他终端。

`WorkspaceCommand` 保留原审批、工作区、输出和沙箱限制。正常脚本中的多行代码、中文及空格路径继续走真实 Broker，不使用宿主兜底。[冻结版本真实探针](../benchmarks/output/budget-close-real-probes-sealed.json)覆盖 LF/CRLF 命令拒绝、脚本末行副作用、末行异常非零退出、单行引号、预算收尾和未结束工具阻止收尾，共 7 项；运行后本 owner 资源清理完成，外部模型请求为 0。初次验证产物另存，不覆盖。

Broker 初始状态为 Stopped，当前终端启动被 Windows 拒绝。用户在管理员 PowerShell 启动后，上述探针通过；本阶段没有改动 Broker 原生实现或要求重装。

## 同一总额内预留验收额度

验收合同新增可选 `reserved_tool_calls`，默认 0，严格整数，不能大于合同 `max_tool_calls`。Runtime 的实际总上限仍取 Runner 与合同中的较小值；预留不增加总额度。

```json
{
  "checks": [{"id": "tests", "kind": "command", "cmd": "python tests_public.py", "timeout_seconds": 20}],
  "max_repairs": 0,
  "max_model_turns": 16,
  "max_tool_calls": 12,
  "reserved_tool_calls": 2
}
```

此例 Agent 最多使用 10 次调用，验收可以使用剩余总额度。验收启动、空输入轮询和非空 stdin 都计入原工具总额；文件/JSON 检查沿用原直接检查语义。预留 2 次并不保证任意长命令或多条检查足够，额度不足会明确结束并取消尚未结束的验收命令。

每次模型决策前，合同上下文携带当前总用量、剩余额度、Agent 可用额度、验收消耗和剩余模型决策。已经执行的验收调用不会再次计入 Agent 消耗，但仍占总额度。超过 Agent 额度的工具批次整批拒绝，不产生部分副作用。

验收调用 ID 在工具执行前随状态保存；阶段用量由实际调用列表与这些 ID 推导。合同、调用 ID 和收尾原因随 SQLite 检查点持久化，恢复不重新发放额度。恢复发现已经开始预算收尾时，停止并记录原因，不重新发起模型请求或自动重放验收命令。

## 确定性收尾

只有预留大于 0 的验收合同启用预算边界收尾。Agent 额度耗尽、工具批次不满足额度或模型决策额度耗尽时，Runtime 记录 `acceptance_handoff`，直接执行固定验收，不调用模型生成最终总结。

- 所有验收通过，且没有取消、截止时间、未完成工具或未完成 Todo 时，使用固定文字完成任务，并保留触发原因。
- 验收失败、阻塞或额度不足时结束；此路径不再分配模型修复机会，即使合同仍有名义修复次数。
- 正常的模型提交和合同内修复继续遵循原规则，全部消耗累计额度。
- 预留为 0 时保留原预算结束策略。

报告的 `execution_budget` 区分 Agent、验收和总消耗，`handoff_reason` 区分 `agent_tool_limit`、`model_turn_limit` 与 `tool_batch_limit`。固定验收通过是执行层完成依据，独立评分仍在 Agent 退出后进行，不因自动收尾而省略。

## 固定候选与策略对照

新增 [harness_budget_finish_v1.json](../benchmarks/harness_budget_finish_v1.json)，由 [候选生成器](../benchmarks/budget_cases.py)确定性生成。共 4 类工程任务、8 份固定缺陷候选：

| 任务类型 | 缺陷候选 |
| --- | --- |
| 配置 | 布尔值写成字符串；重试次数错误 |
| 数据处理 | 累加了取消订单；遗漏订单 |
| 代码修复 | 去重结果被排序；原地修改输入 |
| 多文件一致性 | package 版本错误；名称字段被覆盖 |

所有候选先通过真实公共筛查确认失败，参考解均须通过。数据输入与公开代码测试文件另做字节保留检查。候选与独立 rubric 在付费试验前冻结，全部纳入免费校准，不按模型结果筛选。这是维护者编写的开发回归，不能称为盲测。

`budget_finish_v1` 协议使用 `shared` 与 `reserved` 两组：共同初始候选、初始公开失败报告、提示文本、模型、工具、最终公开检查、总额度、截止时间及独立 rubric 一致。唯一策略差异为预留 0/2 次以及相应边界收尾。两组都没有合成提交或额外自动修复机会。

[冻结版本免费真实校准](../benchmarks/output/budget-close-sealed-reference.json)覆盖全部 8 份候选 × 2 组，16/16 参考 trial 通过；外部模型请求为 0。脚本参考用于机制验证，不计为模型能力成绩。

新增 `action_efficiency` 统计相同参数读取、相同参数命令、公开自测、无已记录文件修改时的再次公开自测、编辑冲突和多行命令请求。计数只描述轨迹，不能据此断言执行无效；没有自动缓存或跳过任意命令。

## 验证与使用

新增测试覆盖严格预留校验、工具批次原子拒绝、模型额度边界、取消/截止、收尾失败不分配修复、恢复阻止重放、真实子进程轮询耗尽和清理、跨修复轮次的阶段计数。真实 loopback HTTP 对照验证两组同初始反馈、独立评分在退出后执行，以及预算收尾无额外模型请求。

后端全量 **1788 通过、80 跳过**，见 `.pytest-budget-close-full.log`。全量运行期间补充的恢复边界、上下文余额修正及轮询/阶段计数用例由最终 **76 项相关回归通过**覆盖，见 `.pytest-budget-close-sealed.log`；不把全量早期加载的模块说成已经覆盖后续每一处修改。另有 81 项相关回归记录保留。跳过项包含需显式开启的 Broker、管理员令牌、隔离 MCP 环境、平台与链接权限等，不能据此认定这些环境已经验证。

前端类型检查、69 个文件的 557 项测试、生产构建通过，见 `.typecheck-budget-close.log`、`.frontend-tests-budget-close.log`、`.frontend-build-budget-close.log`。Ruff 检查通过，569 个 Python 文件格式检查通过。真实服务与子进程探针单独验证，单元测试与 loopback HTTP 不调用付费 API。

执行源码指纹 `c4249f5e223ea422be0040849eb84963513de4c5f666dfa71c69f7ad64d4b96e`，914 个白名单文件，见 [执行前归档与计划](../benchmarks/output/budget-close-source-baseline.json)。预先选定 `repair-stable-unique-sorted` 与 `release-version-package-version`，各运行 shared/reserved 一次，共 4 次；均来自已冻结的 8 份候选。模型和参数沿用上一阶段，16 个模型决策、12 个总工具调用、并行度 1、trial 时限 120 秒、transport 重试 0。上一阶段的结果保持独立。

本阶段在原共享账本增加 ¥3 子预算，仍受用户已授权的总 ¥20 限制。每次 HTTP 先进行最大费用预留，未知请求保留全额，预算拒绝停止后续 trial。没有重置旧账本或释放历史未知预留。

## 真实模型校准结果

实验 `3825ca8dad6f4dfa9ad9b32d901c1459` 的 [JSON](../benchmarks/output/volcengine-budget-close.json)、[Markdown](../benchmarks/output/volcengine-budget-close.md)、[JSONL](../benchmarks/output/volcengine-budget-close.trials.jsonl)保留。预定 4 次，实际全部执行结束，没有模型错误、环境阻塞、超时或费用预算拒绝。

| 指标 | shared | reserved |
| --- | ---: | ---: |
| 完成且独立评分通过 | 2 / 2 | 2 / 2 |
| 最终产物通过 | 2 / 2 | 2 / 2 |
| 实际模型请求 | 10 | 9 |
| 实际工具调用 | 11 | 11 |
| Token | 39426 | 34675 |
| 保守费用估算 | ¥0.123670 | ¥0.110602 |
| 重复参数读取 | 2 | 2 |
| 重复参数命令 / 多行命令 / 编辑冲突 | 0 / 0 / 0 | 0 / 0 / 0 |
| 预算边界收尾触发 | 0 | 0 |

两对配对均双方通过，没有观察到完成率差异。此次模型较早正常提交，没有达到预算边界，因此不能宣称真实模型试跑验证了成功率改善或稳定成本收益。预算边界本身由真实 Broker 探针和故意持续读工具的 loopback HTTP 实验覆盖；它们证明机制，不计为付费模型能力结果。后续可加入较长任务和预算压力场景，继续独立评估。

[离线复核](../benchmarks/output/budget-close-offline/audit.json)覆盖 4 份最终、4 份检查前、2 份共同初始候选，共 **10 份**。状态、rubric 指纹和逐 case 结果一致，两份初始缺陷候选仍失败。逐次 JSONL 与最终报告一致；同题两组的初始文件指纹、公开提示哈希和初始公开证据相同，阶段调用数与工具证据匹配，外部模型请求为 0。

本阶段 19 次真实 HTTP 请求均有完整用量，新增保守估算 **¥0.234272**，¥3 子预算剩余 **¥2.765728**。历史累计 405 次请求，已知估算 ¥6.704900，加上原未知请求预留 ¥2.113536，合计占用 **¥8.818436**，总 ¥20 余额 **¥11.181564**。没有新增未知预留；这些是固定费率下的估算和预留，不是核对过的账单。

[交付审计](../benchmarks/output/budget-close-delivery-audit.json)核对源码 ZIP 逐文件哈希、冻结版本、候选证据、阶段计数、19 次请求关联和原账本增量。扫描 1577 个源码、文档、新证据及日志文件，未发现当前 Provider key 明文。未创建 commit、push 或触发远程 CI。

本次真实执行命令留档如下（文件已存在，CLI 拒绝覆盖；后续运行必须使用新文件名并核对剩余额度）：

```powershell
uv run python -m benchmarks.live --budget-cny 20 --experiment-budget-cny 3 --suite benchmarks/harness_budget_finish_v1.json --task-id repair-stable-unique-sorted --task-id release-version-package-version --repeat 1 --max-model-turns 16 --max-parallel-tools 1 --trial-timeout-seconds 120 --temp-root benchmarks/output/budget-close-temp-20261104 --with-commands --output benchmarks/output/volcengine-budget-close.json
```

免费探针与参考：

```powershell
uv run python -m benchmarks.budget_probe --temp-root benchmarks/output/my-temp --output benchmarks/output/my-integrity.json
uv run python -m benchmarks.harness --suite benchmarks/harness_budget_finish_v1.json --max-tool-calls 12 --temp-root benchmarks/output/my-temp --output benchmarks/output/my-budget-reference.json
```

本机磁盘预留策略保持启用；F 盘进程临时目录不修改系统或用户全局配置。已有产物保留，CLI 拒绝覆盖。报告 schema 为 7；旧协议和旧试跑原始报告不回填、不混算。
