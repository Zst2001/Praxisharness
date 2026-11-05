# 逐轮证据与固定候选修复评测

后续资源准入诊断、遗弃许可回收、公开输出聚合、等机会重试对照及实验子预算见 [下一阶段记录](harness-feedback-control.md)。本页历史源码、设置和费用保持原口径。

日期：2026-11-04（Asia/Shanghai）。本轮落实逐轮候选快照、真实反馈修复专项，以及两类任务的冻结独立回归检查。命令运行中崩溃恢复、实验前端详情和效率优化仍属于后续范围。

## 逐轮证据

Runtime 新增 `acceptance_checking` 事件，在每轮任何检查执行前发布，携带检查轮次和已消耗修复次数；沿用原事件、检查点与审计路径。原检查条件、审批、累计预算及恢复规则不变。

基准执行器监听该事件，在 Agent 工作区之外保存 `rounds/01` 至最多 `rounds/06` 的候选快照。第一轮差异以初始夹具为基线，后续轮次以前一轮候选为基线，包括额外文本文件的删除。每轮保存：

```text
<trial>.evidence/.../<attempt>-<arm>/
├─ manifest.json
├─ candidate/                   # 最终候选
├─ rounds/
│  ├─ 01/
│  │  ├─ manifest.json
│  │  ├─ candidate/             # 第一次公开检查前的候选
│  │  ├─ diff.patch
│  │  ├─ tools.json
│  │  ├─ acceptance.json        # 实际公开检查报告
│  │  ├─ feedback.json          # 仅在分配反馈修复时生成
│  │  └─ independent.json       # Agent 退出后才生成
│  └─ 02/...
├─ grading.json
└─ trial.json
```

`feedback.json` 保存公开报告、实际修复分配事件及合同状态，不是模型请求体的逐字备份，不含独立 rubric。各轮独立评分统一在 Agent 退出后运行，不将评分结果反馈给本轮模型。

每个快照沿用 16 文件、单文件 1 MiB、候选总计 4 MiB、目录扫描 256 条目上限；最多六轮加最终快照，候选数据合计最多 28 MiB，另有有界工具预览和元数据。原有脱敏、链接/硬链接拒绝和不完整证据拒绝精确重评分继续生效。父清单保存轮次与子清单哈希，子清单校验候选和附件；哈希不等于数字签名。

报告 schema 为 5，新增 `evaluation_protocol`、`synthetic_submission_decisions` 和 `repair_analysis`。记录首轮公开及独立状态、实际分配的反馈修复次数、公开检查前后累计计数、首次提交后的额外请求/Token/保守费用和耗时，以及各轮解决和新引入的失败 case。若候选修改后因预算耗尽未进入下一轮公开检查，也会将最终独立结果与上一快照比较。

首次提交后的额外开销包含公开检查，不包含后续独立评分；独立评分耗时计入 `grading_duration_ms`。未知 Token 保持缺失，未知请求的费用预留单独记录。没有实际 Provider 请求的合成提交不伪造 Token 用量。

## 两个协议分开统计

`harness_challenge_v3.json` 是正常端到端执行：模型从初始缺陷代码开始，自行检查、修改和提交。

`harness_repair_v1.json` 是 `submitted_candidate_repair_v1` 条件修复专项。两题各固定一份预定义缺陷候选：金额分配把余数相同时的索引优先级反转；拓扑排序使用 FIFO 处理新就绪节点，违反每一步选取最小节点的要求。候选纳入规则与源码在试跑前固定，包含全部两份候选，不按后续模型结果筛选。

| 组别 | 条件修复专项的流程 |
| --- | --- |
| baseline | 原样提交固定候选后退出，不发模型请求 |
| verify | 原样提交，公开检查后停止，不发模型请求 |
| repair | 原样提交，公开检查失败后，才允许模型在剩余预算内修复 |

共同首次提交由合成 Planner 完成，占用一个模型决策轮次，但没有 Provider 请求。repair 之后的实际请求仍通过现有 LLMPlanner、Provider、工具和真实 Broker。公共预算包括这次合成决策与所有验收工具轮询，不在反馈阶段重新发放额度。

该专项衡量“给定失败提交后的条件修复”，baseline 与 verify 失败是夹具设计的结果，不能转写成端到端成功率提升。它也没有排除额外尝试机会的影响；若研究反馈本身的因果作用，还需要无反馈重试对照。

## 冻结回归检查

两套清单使用完全相同的公开检查与独立 rubric，覆盖金额分配和拓扑排序。公开样例检查输入不可变性，并补齐索引平局、零权重、空输入、较大整数、重复边、新就绪节点和部分成环等要求。

独立用例使用另一组输入，金额分配 7 个、拓扑排序 8 个，均有稳定 case ID；另检查公开文件字节未被修改。两套清单存储 `independent_rubric_sha256`，加载时校验，修改 case 后保留旧指纹会被拒绝。它们是维护者编写、试跑前冻结的开发回归集，不是盲测或公开能力基准。

## 使用入口

免费真实 Broker 校准：

```powershell
uv run python -m benchmarks.preflight --sandbox-smoke --output benchmarks/output/my-rounds-preflight.json
uv run python -m benchmarks.harness --suite benchmarks/harness_repair_v1.json --output benchmarks/output/my-rounds-reference.json
```

重评分某个检查前的候选，模型请求数为 0：

```powershell
uv run python -m benchmarks.evidence --manifest <最终manifest.json绝对路径> --round 1 --suite benchmarks/harness_repair_v1.json --output benchmarks/output/my-round1-regrade.json
```

使用已保存的 Provider 和原共享预算账本进行真实试跑：

以下为当前版本入口；复现历史试验需使用其对应源码归档和参数，当前默认调度与等待时限不能视为首批旧配置。

```powershell
uv run python -m benchmarks.live --suite benchmarks/harness_repair_v1.json --repeat 2 --max-model-turns 16 --with-commands --output benchmarks/output/my-real-repair.json
uv run python -m benchmarks.live --suite benchmarks/harness_challenge_v3.json --repeat 2 --max-model-turns 16 --with-commands --output benchmarks/output/my-real-regression.json
```

后两条会产生费用。含代码执行的任务仍在任何模型请求前通过真实 Broker 预检，没有宿主命令降级。输出使用新文件名，不覆盖历史试验。

## 验证与执行基线

[真实预检](../benchmarks/output/preflight-rounds-v1.json)通过工作区写入与读回、只读拒写和工作区外拒写。两题的 [修复专项参考报告](../benchmarks/output/rounds-repair-reference.json) 与 [端到端参考报告](../benchmarks/output/rounds-challenge-reference.json) 均表现为脚本 baseline/verify 失败、repair 两题通过。初始候选同时被公开和独立检查拒绝，参考解均通过；这些是免费机制校准，不计入真实模型能力。

新增测试覆盖检查事件先于命令副作用、两题校准、逐轮源码与反馈、按轮重评分、清单篡改、冻结用例修改、删除文件差异、修复引入新错误及预算中断后的最终候选。真实 loopback HTTP 测试验证只有 repair 发送请求，模型请求不含独立 case，逐轮独立评分发生在运行退出后。

首批条件修复与中断回归的执行源码指纹为 `09c7776f7bce2a068056ca687ced478458822804cb08059f196da1ef9a90b571`，白名单文件 901 个，[归档基线](../benchmarks/output/rounds-source-baseline.json)保存逐文件清单和执行前费用快照。后续等待时限修复另行归档，历史源码和结果保留。未创建 commit 或 push。

后端全量回归 **1741 通过、80 跳过**，见本地 `.pytest-rounds-full.log`；全量收集后追加的预算中断边界用例另行 **1 通过**。相关集中回归 **55 通过**，见 `.pytest-rounds-final.log`。跳过项涉及默认未启用的真实隔离/可选环境测试、管理员令牌及平台链接权限等，不将这些项目视为已验证。

前端类型检查通过，**69 个文件、557 项测试通过**，生产构建通过；见本地 `.typecheck-rounds.log`、`.frontend-tests-rounds.log` 和 `.frontend-build-rounds.log`。构建保留现有大 chunk 提示，未因此修改无关打包配置。Ruff 检查及 558 个 Python 文件格式检查通过。

## 真实条件修复结果

固定模型 `deepseek-v4-1-flash-261017`，temperature 0、输出上限 2048、上下文 32768，关闭 thinking，transport 重试为 0。两题 × 两次 × 三组，共 12 个 trial；每组上限 16 个模型决策（包括一次合成提交）、12 次工具调用，repair 最多两次反馈修复。

原始报告：[JSON](../benchmarks/output/volcengine-rounds-repair.json)、[Markdown](../benchmarks/output/volcengine-rounds-repair.md)、[JSONL](../benchmarks/output/volcengine-rounds-repair.trials.jsonl)。实验 ID `0c371022cfd848cab5cded07fb2a4f1d`。

| 候选 | 实际触发反馈修复 | 最终产物独立通过 | 流程完成且独立通过 |
| --- | ---: | ---: | ---: |
| 金额分配 | 2 / 2 | 2 / 2 | 0 / 2 |
| 拓扑排序 | 2 / 2 | 2 / 2 | 2 / 2 |

四次真实模型修复均由首轮公开失败触发；首轮独立评分同样失败。金额分配的低索引平局 case、拓扑排序的新就绪顺序和多依赖 case 在最终候选中修复。冻结用例中未观察到新增失败。

两次金额分配产物已正确，但 12 次工具额度耗尽，未完成新的 Harness 验收，仍计 `budget_exhausted`，不计成功。首轮公开检查分别占 5 次、3 次工具调用，余下 Agent 工具分别占 7 次、9 次；第一次还出现一次额外命令非零退出。工具轨迹记录了重复运行公开测试等操作，但仅凭结果不能把全部预算消耗归因于模型或某个系统因素。

两次拓扑排序经过一轮反馈修复和第二轮公开验收，正常完成且独立通过。baseline 与 verify 的 8 个 trial 保留固定缺陷，均失败，且未发出模型请求。条件修复的完成成功为 2 / 4，最终产物正确为 4 / 4；这是两份特定缺陷、每份两次的小样本观察，不是端到端提升，也未单独识别反馈的因果收益。

本专项共 23 次实际 HTTP 请求，用量均完整，新增保守估算 ¥0.426264，无新增未知请求预留。前半段试跑与本地回归测试并行，系统负载可能影响时间及轮询次数，本轮不做性能优劣比较；端到端回归在全量检查结束后另行运行。

## 端到端回归中断与等待时限

原计划两题 × 两次 × 三组，共 12 个端到端 trial。实验 `7db0747c458d43ffb1b70fc9a3290ca8` 完成 7 条记录后，第 8 条（金额分配第二次 baseline）长时间卡在资源准入，尚有 4 条未运行。原 [JSONL](../benchmarks/output/volcengine-rounds-regression.trials.jsonl) 和 [不完整批次记录](../benchmarks/output/volcengine-rounds-regression-interrupted.json) 保留；不把未运行项补成失败或并入完整批次。

调用栈显示两个工具线程均位于 `SandboxLauncher.wait_resources`，主线程等待工具批次完成；Broker 控制面健康，未观察到新的命令子进程。采样方式参考 [py-spy 官方 dump 说明](https://github.com/benfred/py-spy#dump)，本地保留的 `.rounds-regression-stack.log` 只含调用栈。现有证据能定位等待位置，尚不能确定服务端未授予许可的完整根因。

停止本轮自有试跑进程前，核对没有未结束的 Provider 请求；保存当前候选后，仅结束该试跑及本轮等待中的重评分辅助进程。未停止 Broker、修改服务配置、放宽配额或执行宿主兜底。中断候选已在真实 Broker 中 [离线重评分](../benchmarks/output/rounds-interrupted-regrade.json) 通过；它仍是中断记录，不能计为执行成功。原临时目录保留供复核。

基准执行器补上默认 120 秒的 trial 协作式取消，覆盖模型/工具执行及资源排队，超时分类为 `trial_timeout` 并停止后续 trial，报告 `complete=false`、计划数和实际记录数。使用既有取消路径撤销资源等待，不凭空重放调用。独立 Python 评分增加包含准入的 15 秒截止时间，避免检查自身卡在排队阶段。独立评分超时标为阻塞，区别于代码结果错误。

协作式取消不等于 OS 强杀硬截止：返回时间还受当前操作响应取消及清理耗时影响，Provider 仍有自己的 60 秒超时。自动终止 API 请求后产生的未知费用仍保留完整预算预留。

复测统一采用 `max_parallel_tools=1` 的串行工具调度，并记录到配置指纹；该配置与首批修复专项不同，结果不混算。增加等待截止与批次停止的相关回归 **97 通过**，串行调度后的相关回归 **34 通过**，见 `.pytest-rounds-bounded.log` 和 `.pytest-rounds-serial.log`；覆盖资源等待取消、后续 trial 不继续发请求，以及独立评分准入超时。

最终执行源码指纹为 `3a0fac5a288bfcd18ba50a09a62750f7846ecf195e96594ba93d88e0e97a3ef6`，901 个白名单文件；[收敛后归档基线](../benchmarks/output/rounds-bounded-source-baseline.json)保留新版本、协议参数及复测前费用。复测范围缩为两题各一次 × 三组，共 6 条，输出使用独立的新文件名。

## 收敛版本的端到端结果

实验 ID `13e9a12ba45943f9a4bd10c1435c2155`，原始 [JSON](../benchmarks/output/volcengine-rounds-regression-bounded.json)、[Markdown](../benchmarks/output/volcengine-rounds-regression-bounded.md)、[JSONL](../benchmarks/output/volcengine-rounds-regression-bounded.trials.jsonl) 均保留。六条全部执行结束，未触发 trial 总时限，也未发生环境阻塞或模型请求错误。

| 组别 | 完成且独立评分通过 | 最终产物独立评分通过 |
| --- | ---: | ---: |
| baseline | 1 / 2 | 2 / 2 |
| verify | 2 / 2 | 2 / 2 |
| repair | 2 / 2 | 2 / 2 |

baseline 的金额分配 trial 因工具预算耗尽未完成，产物仍通过。verify 与 repair 均首次验收通过，未触发反馈修复，没有误接受。该结果与前述固定失败提交专项分别解释；单题每组一次、调度配置变化及中断后的复测均限制了比较结论，不能宣称总体成功率或反馈收益提升。

本轮 47 次实际 HTTP 请求均返回用量，新增保守估算 ¥0.823436。串行小样本未复现卡顿，不等于底层并发准入根因已经修复；当前保护是评测侧取消时限、停止后续付费 trial 和保留不完整报告。

## 离线复核与费用合计

[批量离线复核](../benchmarks/output/rounds-offline-validation-bounded.json)覆盖条件修复完整批次、中断前已完成的 7 条以及新版本 6 条：**25 份最终候选 + 19 份检查前候选，共 44 份**，状态、rubric 指纹和逐 case 结果均与原评分一致，外部模型调用数为 0。执行耗时不要求一致。中断中的第 8 条候选另行首次评分通过，未混入上述“与原评分一致”的计数。

复核同时校验报告/JSONL 一致、候选清单哈希和对应源码版本。中断旧批次与新版本批次各自保留，不将两者合并成一个完成实验。

本阶段费用包含失败、中断及复测，均使用原共享账本：

| 批次 | HTTP 请求 | 有用量的请求 | 新增保守估算 |
| --- | ---: | ---: | ---: |
| 条件修复专项 | 23 | 23 | ¥0.426264 |
| 中断端到端批次 | 67 | 67 | ¥1.250916 |
| 串行且有时限的复测 | 47 | 47 | ¥0.823436 |
| 合计 | 137 | 137 | **¥2.500616** |

全部历史累计 294 次 HTTP 请求，已知用量估算 ¥4.726956，原未知超时预留 ¥2.113536，合计 **¥6.840492**；原 ¥10 预算剩余 **¥3.159508**。没有新增未知请求预留，也未清空账本或释放历史预留；未核对实际账单。

[最终交付核对](../benchmarks/output/rounds-delivery-audit.json)保存分批费用、执行版本、离线复核及脱敏扫描结果。1649 个源码、文档、输出及日志文件和两份源码归档未发现当前 Provider key 明文。后续优先定位底层并发资源准入、保留公开检查的有界输出分片，并减少重复自测和轮询预算消耗。
