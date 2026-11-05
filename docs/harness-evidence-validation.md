# 候选证据、逐项评分与公开验收 V2

日期：2026-11-04（Asia/Shanghai）。本轮针对上次真实代码试跑中“公开验收通过、独立评分失败但没有候选源码”的问题，补齐可复核证据，再检验公开验收覆盖。

## 实现范围

- 每个 trial 在 Agent 退出后、独立评分前保存候选文件、相对初始文件的有界差异、工具摘要和清单；评分完成后追加独立结果与 trial 记录。成功与失败都保留。
- Python 独立评分逐 case 记录 `check_id`、`case_id`、状态及 `failure_kind`，区分结果错误、输入被修改、函数异常、导入异常、评分未完成和环境阻塞。文件保留检查也列入 `case_results`。
- 提供离线重评分 CLI；校验原套件指纹和证据文件哈希，重建临时工作区，在真实 Broker 内执行同一独立评分，不调用模型。
- 新增 `harness_challenge_v2.json`，目前只覆盖 `merge-layers`；原四题 V1 清单与历史报告保留。V2 增强公开要求覆盖，独立 rubric 与 V1 完全相同。

评分仍在任务结束后执行，不进入本轮模型上下文。该评分器用于普通代码行为检查，不是抗恶意提交裁判；文件哈希用于完整性比对，不是签名或来源认证。

## 证据结构与边界

CLI 默认输出与报告同名的 `.evidence` 目录：

```text
<report>.evidence/<experiment_id>/<task_id>/<attempt>-<arm>/
├─ manifest.json
├─ candidate/             # 初始输入、候选产物和有界扫描发现的额外文本文件
├─ diff.patch             # 相对初始文件的有界差异片段
├─ tools.json             # 工具参数白名单、阶段、耗时、退出码及脱敏结果预览
├─ grading.json
└─ trial.json
```

报告 schema 升为 4，记录 `acceptance_revision`、`public_fixture_sha256`、`tool_summary` 和证据清单路径/哈希。工具摘要区分 Agent 自行调用与 Harness 验收调用；非零命令退出码不等于 Harness 反馈修复，修复次数仍以 Runtime 验收状态为准。

最多保存 16 个 UTF-8 文件，单文件 1 MiB、总计 4 MiB，目录扫描最多访问 256 个条目。忽略缓存目录，拒绝链接、reparse point、硬链接和越界路径。超限、二进制、不可读或发生脱敏的文件会使证据标记为不可精确重放，重评分入口拒绝把它当作完整候选。差异与工具结果为有界预览，完整候选以 `candidate/` 为准。

已知 Provider key 与可识别敏感模式会被替换，环境转储命令的结果预览省略；不记录请求认证头、完整环境或模型请求体。该机制不保证识别任意业务秘密，分享前仍应审查具体文件。逐项评分只保留最多 3 个失败的简短实际值/异常类型预览，不保存候选的任意打印或异常消息。

## 复现

不调用模型的真实 Broker 机制验证：

```powershell
uv run python -m benchmarks.preflight --sandbox-smoke --output benchmarks/output/my-evidence-preflight.json
uv run python -m benchmarks.harness --suite benchmarks/harness_challenge_v2.json --task-id merge-layers --output benchmarks/output/my-evidence-reference.json
```

从报告 `results[*].evidence.manifest` 取得清单路径后，离线重评分：

```powershell
uv run python -m benchmarks.evidence --manifest <manifest.json的绝对路径> --suite benchmarks/harness_challenge_v2.json --output benchmarks/output/my-regrade.json
```

重评分必须使用原套件；篡改候选、工具记录或替换套件都会被拒绝。代码仍通过 Sandbox 工具执行，Broker 不可用时不降级到宿主。输出明确记录 `external_model_requests=0`。重新执行的耗时可以不同，比较的是状态、逐 case 结论和 rubric 指纹。

## 公开验收 V2 的变化

V2 对两个输入都做深拷贝前后比较，并用规范 JSON 区分 `false` 与 `0` 等类型；公开样例覆盖递归合并、深层 null 删除、标量替换为字典时的递归处理、数组整体替换及空对象。公开数据与独立 case 数据不同。

构造的校准实现仅在原值也是字典时递归，因此新字典中的 null 会被错误保留。它能通过 V1 公开检查，却不能通过独立 case 2；V2 公开检查能拒绝它。此例是本轮构造的已知缺陷，不是恢复了上次未留存的真实候选，不能反推上次三次误接受的代码原因。

## 本地验证

[真实预检](../benchmarks/output/preflight-evidence-v2.json)报告服务 running / healthy，工作区写入及控制用户读回、只读拒写、工作区外拒写通过；没有模型调用。

真实评分暴露了流式输出问题：评分协议可能在进程退出前已返回，最终命令轮询为空。评分器现从此前有界工具输出中还原协议；新增延迟退出回归测试。首次诊断报告 `evidence-v2-reference.json` 保留，不能把其中 `grader_incomplete` 当作具体行为错误。

修复后 [真实 Broker 参考报告](../benchmarks/output/evidence-v2-reference-streamfixed.json)中，脚本 baseline 与 verify 保留初始错误实现而失败，repair 经一轮反馈写入参考解并通过。三份证据再次经过真实 Broker 重评分，状态和全部逐项结果一致，模型请求数为 0。该参考试验证明机制，不计入真实模型能力成绩。

扩展后端回归 **386 通过、5 跳过**，见本地 `.pytest-evidence-final.log`。其中本轮新增证据测试 10 项。跳过项为两项独立管理员服务测试、一项受保护 Broker 对象测试和两项目录链接权限测试。Ruff 检查与 556 个 Python 文件格式检查通过。未修改前端与生产 Broker，本轮未重跑前端测试或构建。

## 真实模型对照

固定 `deepseek-v4-1-flash-261017`，V1/V2 各 1 次重复 × 3 组，仅选择 `merge-layers`。共同上限为 16 个模型决策、12 次工具调用，repair 最多 2 次修复；thinking 关闭，transport 重试为 0，temperature 为 0。先运行 V1，再运行 V2，使用新的独立工作区并保留全部结果。单题单次样本不支持统计显著或因果提升结论。

执行源码及依赖指纹为 `19da52c5c4f7a5ee9c4167739e3e3e5a71f16b4234124014e84cfc42afb24b94`，897 个白名单文件；[归档与逐文件清单](../benchmarks/output/evidence-source-baseline.json)包含执行前费用快照。Git 尚无提交，未伪造 commit 标识。

V1 试验 ID 为 `3c4f3d6455a14a4883a58083ae49cf38`，原始结果见 [JSON](../benchmarks/output/volcengine-evidence-v1.json)、[Markdown](../benchmarks/output/volcengine-evidence-v1.md) 和 [逐 trial JSONL](../benchmarks/output/volcengine-evidence-v1.trials.jsonl)。三份候选证据均在真实 Broker 中完成离线重评分，逐 case 结果与原评分一致。

V1 baseline 与 verify 的实际候选都只在新旧值均为字典时递归；标量 `7` 被 `{"x": false, "z": null}` 覆盖时直接复制了新字典，实际输出保留 `z: null`，而需求要求删除 `z`。对应独立 case 2 为 `wrong_result`，其他行为 case 与公开文件保留检查通过。verify 的公开验收通过，因此记为一次误接受；baseline 没有 Harness 验收，不计入误接受分母。repair 组首次通过，不能计为反馈修复收益。这是本轮已留存代码支持的定位，不回填上轮无源码证据的结果。

V2 试验 ID 为 `eba50ffef5f343299d426474303f2fea`，原始结果见 [JSON](../benchmarks/output/volcengine-evidence-v2.json)、[Markdown](../benchmarks/output/volcengine-evidence-v2.md) 和 [逐 trial JSONL](../benchmarks/output/volcengine-evidence-v2.trials.jsonl)。两轮都按 verify → repair → baseline 执行；V2 让 baseline 也能看到更强的公开文件并自行运行测试，不能把跨版本变化全归因于 Harness 运行期验收。

| 组别 | V1 完成且独立评分通过 | V2 完成且独立评分通过 |
| --- | ---: | ---: |
| baseline | 0 / 1 | 1 / 1 |
| verify | 0 / 1 | 1 / 1 |
| repair | 1 / 1 | 1 / 1 |

V1 有 1 / 2 次 Harness 验收通过被独立评分判为失败，V2 为 0 / 2。六次试跑均正常结束，没有工具预算耗尽、环境阻塞或模型错误；两轮均没有触发 Harness 反馈修复。单题单次观察支持继续研究公开测试覆盖，不能估计可靠的误接受率降幅、总体成功率提升或反馈修复收益。

六份真实候选全部完成真实 Broker 离线重评分，状态、rubric 指纹和逐 case 结果一致，模型调用数为 0。机器核对摘要见 [对照与完整性记录](../benchmarks/output/volcengine-evidence-comparison.json)，包含两轮输入指纹、同模型/预算/工具校验、JSONL 与 JSON 一致性、证据清单哈希和重评分路径。源码执行期间保持一致，历史报告未改写。

## 费用

本轮新增 38 次 HTTP 请求，全部返回用量，新增保守估算 **¥0.606142**（V1 ¥0.296036、V2 ¥0.310106）。继续沿用原 `volcengine-live-budget.db`，未新建账本、清零或释放未知请求预留。

累计 157 次 HTTP 请求，已知用量保守估算 ¥2.226340，原未知请求预留 ¥2.113536，合计 **¥4.339876**，原 ¥10 预算剩余 **¥5.660124**。这些是按配置单价计算的估算与预留，没有核对实际账单。

## 保留限制

当前 V2 只完善一题；尚未形成多题重复实验。公开测试可被模型看到，成功不能证明未知任务泛化。普通行为评分不能防止有意读取评分进程内部数据。进程在命令运行中退出后的恢复、网络隔离和完整隔离认证仍未单独验证，不能因本轮通过而扩大结论。

后续逐轮快照、两题条件修复、冻结回归检查及资源等待时限见 [下一轮实现与验证](harness-rounds-validation.md)。本页 V1/V2 原始样本与费用快照保留，不回填后续结果。
