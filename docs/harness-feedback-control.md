# 资源准入、输出聚合与等机会反馈对照

后续命令执行完整性、验收预留与等反馈预算策略对照见 [下一阶段记录](harness-budget-finish.md)。本页历史实验保持原版本与统计口径。

日期：2026-11-04（Asia/Shanghai）。本阶段实现资源准入诊断、公开检查输出聚合、无初始反馈重试对照和实验费用子预算。前端详情、命令执行中崩溃恢复不在本阶段范围。

## 资源准入与真实运行

原先已授予但尚未使用的许可不会过期，客户端取得许可后退出，可能一直占据队首。确定性测试复现该状态。修复后，轮询维持等待者存活，超过 30 秒未续期的未认领许可回收；只有可信原生启动器可以认领许可，认领中的启动持续保留，直到 `finally` 释放。取消认领中的启动会禁止注册，清理完成前不放行第二次启动。不会重置配额或清除其他调用的许可。

Broker 返回 `diagnostics_version=1`、排队位置、等待时长、活动/清理中任务数及有界事件。原因区分采样错误、清理未完成、配额、启动中和 FIFO 等待。历史事件仅返回本 backend owner，客户端通知不携带协议 nonce。准入具备截止时间与取消。Runtime 使用单独的 `tool_resource_wait` 进度事件，避免将每次状态通知误计为一次工具调用。

这解决了可复现的遗弃许可问题；上一阶段历史卡顿只有等待栈，不能断言所有历史卡顿都来自同一原因。[第一次重启后的真实探针](../benchmarks/output/admission-verified-feedback.json)通过 2 路、4 路并发、取消等待者和遗弃许可约 30 秒后回收。结束后队列、许可和进程数归零。

随后真实模型试跑发现另一问题：短进程在 PID 枚举后退出，`OpenProcess` 返回错误 87；原有退出处理只捕获 `OSError`，实际 `pywintypes.error` 并非其子类。两处 PID 资源读取现同时捕获这两类异常，只有原有的错误 87 返回已退出进程的零用量；其他错误继续传播并阻止准入。新增测试直接使用真实 pywin32 异常及 Windows API，覆盖退出、访问拒绝和无效句柄。

用户再次重启曾因服务控制句柄权限失败。检查 DACL 确认 Administrators 有启停权限后，用户重新确认提升的管理员令牌为 True，再成功重启。[最终真实探针](../benchmarks/output/admission-final-feedback.json)重新通过 2/4 路并发、取消和过期回收；[脚本参考](../benchmarks/output/retry-feedback-final-reference.json)两组各 2/2 通过。两者均不调用模型，参考结果只证明机制。

C 盘可用空间低于生产磁盘保留阈值。真实探针和本轮评测以 `--temp-root` 将本进程临时目录放到 F 盘，生产磁盘保护保持启用。此参数仅修改 Python 进程的 `tempfile.tempdir`，不改全局配置。历史 C 盘阻塞记录保留，不与 F 盘执行混作性能比较。

## 公开检查输出与计数

命令检查按顺序累积原始输出分片，保留最近最多 8192 字符；拼接后统一脱敏，取最近最多 2000 字符作为预览，另附截断提示和状态文字。首次轮询包含断言、最后一次为空时，在保留上限内的失败证据仍可见；跨分片敏感文本也会整体脱敏。

首次命令等待最多 5 秒，后续轮询逐步增加到最多 10 秒，受剩余检查时间约束。截止时间覆盖准入和实际执行，通过既有 JobScope 取消。真实进程测试覆盖“先输出失败信息、5.3 秒后退出、最终轮询为空”。

实际工具调用仍占原额度。报告将 Agent/验收命令启动、空输入轮询及非空 stdin 写入分开计数；`*_command_starts` 统计 `run_command` 调用，包含准入失败，不等于成功创建的原生进程数。资源进度不增加调用数，没有增加工具上限。本轮短命令无实际轮询消耗，不能声称真实模型轮询费用下降。

## 等机会对照协议

新增 [harness_retry_feedback_v1.json](../benchmarks/harness_retry_feedback_v1.json)，协议 `retry_feedback_v1`，报告 schema 为 6。

| 设置 | retry | feedback |
| --- | --- | --- |
| 初始候选 | 同一固定缺陷候选 | 同一固定缺陷候选 |
| 模型复查机会 | 一次 | 一次 |
| Harness 提供初始公开失败报告 | 无 | 有 |
| 模型自行运行公开测试 | 允许 | 允许 |
| 最终公开验收及独立评分 | 相同 | 相同 |
| 最终验收后自动反馈修复 | 0 次 | 0 次 |

一次复查允许共同预算内的多次模型决策和工具操作，不等于一个请求。retry 只是没有 Harness 注入的初始报告，不能称为完全没有反馈。

两份固定候选分别是金额分配的余数平局索引反转、拓扑排序的新就绪节点 FIFO。全部纳入，不按模型结果筛选；公开检查与独立 rubric 沿用上一阶段冻结版本（独立行为用例分别 7 个、8 个，另检查公开文件字节）。这是维护者编写的开发回归集，不是盲测或公开能力榜单。

创建模型客户端前，真实 Broker 公共筛查必须失败，并确认初始文件字节不变。两组只在初始公开报告注入上不同；筛查中的 assistant/tool 对话不复制到任何一组。初始报告以不可信数据加入提示，不含独立 rubric。每个 trial 独立评分在模型退出后执行，共同初始候选独立评分在全部模型 trial 结束后执行。

两题 × 两组 × 三次，预定 12 次，交替轮换组顺序。固定 `deepseek-v4-1-flash-261017`、temperature 0、输出上限 2048、上下文 32768、关闭 thinking、transport 重试 0。共同上限为 16 次模型决策、12 次实际工具调用、并行度 1、trial 协作式取消 120 秒。没有合成首次提交或额外免费决策轮次。

该协议补足旧条件修复专项中只有 repair 获得模型重试机会的差异。旧结果保持条件修复口径，不改写为反馈本身的因果收益。

## 共享账本和子预算

沿用 `benchmarks/output/volcengine-live-budget.db`。用户追加 ¥10 后，总上限从 ¥10 增至 ¥20；[追加审计](../benchmarks/output/budget-topup-20-20261104.json)与追加前 SQLite 备份保留，没有清空历史消耗或释放旧未知预留。

新增同一账本的实验子预算，每次实际 HTTP POST 前原子检查总预算和实验额度。未知请求保留完整预留，完整用量返回后按保守费率结算。子预算必须在首次请求前配置，重复配置须相同，不能重置或在已有未受限请求后补配。预算拒绝会停止后续 trial。

本阶段合计新增额度最多 ¥4。初始批次使用 ¥4 子预算；修复后新批次从 ¥4 扣除前批消耗后，子上限设为 **¥3.753088**，见 [执行前计划](../benchmarks/output/feedback-retest-plan.json)。前批不继续、不替换失败或补填未运行项。每请求最大预留 ¥2.113536，剩余额度不足预留时可提前停止。以下费用均是固定费率下的保守估算与预留，不是核对过的账单。

## 原中断批次

实验 `19ba4fec29dc48abb2b0fadcce4dc91e` 的 [JSON](../benchmarks/output/volcengine-retry-feedback.json)、[Markdown](../benchmarks/output/volcengine-retry-feedback.md)、[JSONL](../benchmarks/output/volcengine-retry-feedback.trials.jsonl)保留。预定 12 次，实际 3 次、9 次未运行，`complete=false`，trial 超时后自动停止。

| 任务 / 组别 | 原运行结果 | 原独立评分 | 模型请求 | 实际工具调用 | 保守估算 |
| --- | --- | --- | ---: | ---: | ---: |
| 金额分配 / feedback | 完成 | 通过 | 4 | 5 | ¥0.067168 |
| 金额分配 / retry | 环境阻塞 | 阻塞 | 5 | 8 | ¥0.079022 |
| 拓扑排序 / retry | trial 超时 | 阻塞 | 6 | 7 | ¥0.100722 |

资源记录显示 `sampling_error`。15 次 HTTP 请求均有完整用量，68380 Token，新增估算 **¥0.246912**，无新增未知预留。有效配对仅一对且存在环境阻塞，不能判断反馈收益。

[免费重评分](../benchmarks/output/retry-feedback-offline/audit.json)核对 JSONL 逐行一致、父/子清单哈希和同题初始文件指纹，覆盖 3 份最终、2 份检查前、2 份初始候选，共 7 份。最终及检查前候选均通过，初始缺陷候选均失败；原明确通过的评分与重评分逐 case 一致。原阻塞评分是当时环境观测，后来的通过另存，不回填原 trial 或改计执行成功。

原批次源码指纹 `05e6c1690bf21ab762a03061fe80502dfe73889cce49e2132b2cdcbfbd737bf6`，906 个文件，见 [执行前归档](../benchmarks/output/feedback-source-baseline.json)。短进程修复指纹 `8aef919d67cf18769d41a42c405eaad7e0d188904e29793cdeea058e76ac08f3`，907 个文件，见 [修复归档](../benchmarks/output/feedback-exit-source-baseline.json)。两版本仅 PID 采样处理及新增测试不同。

## 修复后的完整对照

实验 `79101cf0cbe94d84af4f1de0cac81a0f` 的 [JSON](../benchmarks/output/volcengine-retry-feedback-retest.json)、[Markdown](../benchmarks/output/volcengine-retry-feedback-retest.md)、[JSONL](../benchmarks/output/volcengine-retry-feedback-retest.trials.jsonl)分别保留，执行前参数见 [预定计划](../benchmarks/output/feedback-retest-plan.json)。使用修复归档 `8aef919d67cf…`，12/12 次均已执行，`complete=true`，无环境阻塞、trial 超时、模型错误或费用预算拒绝。该完整批次与上述中断批次分别统计。

| 指标 | retry | feedback |
| --- | ---: | ---: |
| 完成且独立评分通过 | 5 / 6 | 6 / 6 |
| 最终产物独立评分通过 | 6 / 6 | 6 / 6 |
| 实际模型 HTTP 请求 | 41 | 36 |
| 实际工具调用 | 51 | 45 |
| Agent `run_command` 调用 | 21 | 13 |
| 最终验收 `run_command` 调用 | 5 | 6 |
| 命令轮询 / stdin 写入 | 0 / 0 | 0 / 0 |
| 总 Token | 223698 | 170585 |
| 保守费用估算 | ¥0.880748 | ¥0.616012 |
| 平均 trial 耗时 | 41.17 秒 | 31.11 秒 |

6 对配对中，5 对双方执行成功，1 对仅 feedback 执行成功。差异出现在金额分配第三次 retry：自行检查和编辑共用完 12 次工具额度，未进入最终验收；退出后的独立评分仍通过。原工具上限保持不变，未补发验收或修复额度。反馈组未触发验收后的自动修复，因为该协议上限为 0。

所有 12 份最终产物均正确，不能将执行完成差异表述为代码正确率提升。费用、Token 和耗时是这两份固定候选三次重复的描述性结果；题目只有两道，服务缓存、请求时延和模型变化仍会影响观察，不能据此宣称整体成功率、稳定成本降幅或统计显著收益。该轮执行期间没有并行运行 pytest 或前端测试。

[免费离线复核](../benchmarks/output/retry-feedback-retest-offline/audit.json)覆盖 12 份最终候选、11 份检查前候选、2 份共同初始候选，共 **25 份**。全部状态、rubric 指纹与逐 case 结论一致；初始候选仍独立失败。源码与执行前冻结版本一致，JSONL 与最终结果逐行一致，原证据文件未改写。加上中断批次的 7 份，本阶段共免费复核 **32 份**，外部模型请求为 0。

## 费用与交付核对

| 批次 | 实际 HTTP 请求 | 有完整用量 | 新增保守估算 |
| --- | ---: | ---: | ---: |
| 原中断批次 | 15 | 15 | ¥0.246912 |
| 修复后的完整批次 | 77 | 77 | ¥1.496760 |
| 本阶段合计 | 92 | 92 | **¥1.743672** |

本阶段 ¥4 子预算剩余 ¥2.256328。历史累计 386 次 HTTP 请求，已知估算 ¥6.470628，加上原未知超时预留 ¥2.113536，共占用 **¥8.584164**；总 ¥20 预算剩余 **¥11.415836**。没有新增未知预留，没有清空旧账本，未核对实际账单。

[交付审计](../benchmarks/output/feedback-delivery-audit.json)核对两份源码 ZIP 的逐文件清单及哈希、试跑/JSONL/请求关联、原账本增量和子预算。扫描 1300 个白名单源码、文档、新证据与日志文件，未发现当前 Provider key 明文。文档后续仅补充结果，执行源码保持冻结。

## 验证范围

后端全量原记录 **1749 通过、80 跳过、10 失败**（`.pytest-feedback-full-C.log`）；10 个模拟 Broker/ACL 用例被 C 盘磁盘保护提前拦截。仅在这些模拟测试隔离磁盘环境后，相关复验 **73 通过、1 跳过**（`.pytest-feedback-confirm.log`），生产保护保持启用。最新采样修复及相关回归 **74 通过**（`.pytest-feedback-exit.log`）。不将原全量命令表述为全部通过。

F 盘全量尝试出现 HTTP/SQLite 时序失败后已停止，仅结束本轮自有测试进程；对应 loopback 测试在 C 盘复验通过。唯一临时目录及失败日志保留，未清理其他任务目录。

前端类型检查、69 个文件的 557 项测试及生产构建通过，见 `.typecheck-feedback.log`、`.frontend-tests-feedback.log`、`.frontend-build-feedback.log`。Ruff 检查通过，563 个 Python 文件格式检查通过。单元测试与 loopback 模型测试不调用付费模型，真实隔离依据单独的 Broker 探针。

新测试覆盖初始筛查通过即拒绝模型试跑、等机会、初始工具历史不进入请求、独立用例不进入模型上下文、共同初始独立评分在全部 trial 后执行、检查点不被后续评分改写，以及子预算拒绝后不再发 HTTP。没有创建 commit、push 或触发远程 CI。

## 使用入口

免费真实环境探针与流程参考：

```powershell
uv run python -m benchmarks.admission_probe --queue-scenarios --temp-root benchmarks/output/my-temp --output benchmarks/output/my-admission.json
uv run python -m benchmarks.harness --suite benchmarks/harness_retry_feedback_v1.json --temp-root benchmarks/output/my-temp --output benchmarks/output/my-reference.json
```

本机总账本已为 ¥20，CLI 须显式传 `--budget-cny 20`。后续真实试跑须使用新文件名，在剩余阶段额度内设置 `--experiment-budget-cny`，不能删除历史输出或改用空账本。重评分入口为 `benchmarks.evidence`，需要对应套件、完整证据和真实 Broker，不调用模型。

本次完整批次的实际命令留档如下；该输出已存在，CLI 会拒绝覆盖。未来执行应重新核对余额和上限。

```powershell
uv run python -m benchmarks.live --budget-cny 20 --experiment-budget-cny 3.753088 --suite benchmarks/harness_retry_feedback_v1.json --repeat 3 --max-model-turns 16 --max-parallel-tools 1 --trial-timeout-seconds 120 --temp-root benchmarks/output/feedback-temp-20261104 --with-commands --output benchmarks/output/volcengine-retry-feedback-retest.json
```

下一阶段可优先研究验收预算分配和减少重复自测：本轮唯一未完成项的产物已经正确，瓶颈在工具使用与终止策略。先扩大固定候选与任务覆盖，再评估这些策略是否稳定改善完成率；前端展示和命令运行中恢复继续作为独立需求。
