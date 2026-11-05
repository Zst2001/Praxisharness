# Harness 后续实现与验证

最新进展：Broker 已恢复，输出读回、参考解运行与清理均通过，6 次真实代码评测已完成。最终结果和费用见 [Broker 与代码评测记录](harness-code-evaluation.md)。下文保留各阶段的诊断过程与当时的阻塞状态。

本轮承接真实模型首轮试跑后的三项优先任务：修正统计与错误诊断、恢复 Broker 命令准入、准备并运行小批评测。原始 9 次试跑及其源码归档保持原样，不能用新统计逻辑改写历史实验结果。

## 统计与请求诊断

- 评测报告升级为 schema version 3。`usage` 汇总同一 trial 中全部模型请求尝试，包含上下文整理等内部调用，不再读取最后一轮响应作为整个任务用量。
- 按 exchange 与 attempt 关联请求和响应，重试中的未知请求单独计数。缺少 Provider 用量的字段保留 null；可用的本地估算单独放在 `estimated_usage_for_unknown_attempts`，不混入 Provider 总量，也不用于释放费用预留。
- Token 均值只纳入用量完整的 trial，并显示覆盖数量、未知尝试数量。修复触发次数、修复成功数及成功率单独报告；未触发修复时成功率为 null。
- 捕获模型失败事件中的错误类型、阶段、exchange ID、尝试序号和 HTTP 状态，避免运行时捕获错误后最终 JSON 的 `error` 为空。Provider 故障优先归类为 `model_error`，不会因后续验收被阻塞而误归类为环境故障。费用拦截归为 `budget_exhausted`。
- 错误仅投影脱敏后的有限字段，不复制消息、认证头、完整请求/响应或 traceback。

本地 HTTP/SSE 夹具完成了 3 次 trial、5 次真实 loopback HTTP 调用。各 trial 累计 Token 分别为 15、45、15，修复组经过失败反馈后成功。报告明确标记为 `local_http_fixture`，外部模型调用数为 0，仅用于链路验证：[JSON](../benchmarks/output/next-local-http.json)、[Markdown](../benchmarks/output/next-local-http.md)。

## 费用关联

原 ¥10 账本继续使用，不清零、不释放旧超时请求的预留。新增 `attempt_details` 保存新请求的 experiment、task、arm、trial attempt、exchange、HTTP attempt、时间、状态及 Provider request ID，不保存提示词和凭据。

每个 trial 导出对应的 HTTP 次数、已知用量保守估算、未知费用预留及请求记录。汇总明确区分估算、未知预留和剩余预算；明细金额单位标为 micro-CNY。历史 42 次请求没有这些关联字段，不伪造历史映射。

## 新增代码评测集

保留原有 12 道工程题作为既有实验与回归入口，另增 `benchmarks/harness_challenge_v1.json`：

| 题目 | 公开测试之外的主要检查 |
| --- | --- |
| 递归配置合并 | null 删除、数组替换、false/0 保留、嵌套值和输入不被修改 |
| 整数金额分摊 | 最大余数、稳定平局顺序、零权重、大整数、总额守恒 |
| 依赖拓扑排序 | 重复边、孤立节点、稳定顺序、自环及空输入 |
| 发布版本解析 | 预发布前导零、build 前导零、非法格式及标识符 |

模型能看到公开测试，独立边界测试仅在任务结束后生成和执行；检查公开测试文件未被修改。参考解已通过公开和独立检查。脚本规划器的四次反馈修复只证明机制，不作为真实模型收益。

付费入口新增 `--suite`、`--task-id`、`--repeat`、`--max-model-turns`、`--with-commands`。含命令或 Python 独立评分的任务必须通过真实 Broker 权限探针，失败时在模型调用前拒绝执行。仍不提供宿主命令降级。

```powershell
uv run python -m benchmarks.live --suite benchmarks/harness_challenge_v1.json --task-id merge-layers --repeat 2 --max-model-turns 16 --with-commands --output benchmarks/output/volcengine-challenge-next.json
```

此命令会产生费用，沿用原账本。只在真实准入通过后执行；本轮实际执行结果见下方记录。

## Broker 定位与修复

服务启动后，真实探针将旧的通用错误定位到 `AuditQueryPerUserPolicy` 错误 2。[微软文档](https://learn.microsoft.com/windows/win32/api/ntsecapi/nf-ntsecapi-auditqueryperuserpolicy)说明其含义为指定账户尚无用户级审计策略。现补上首次初始化，保留已有成功审计设置；访问拒绝、参数错误及设置失败仍阻断执行。

随后真实探针定位到 `AuditQueryGlobalSaclW` 错误 2。新增资源审计 ACL 初始化路径，并在设置后重新查询，核对相同账户 SID、failure ACE 与所需访问掩码；回读不匹配仍阻断。[该查询接口](https://learn.microsoft.com/windows/win32/api/ntsecapi/nf-ntsecapi-auditqueryglobalsaclw)返回的缓冲区改用文档规定的 `LocalFree` 释放。

相关测试覆盖首次初始化、保留已有策略、权限错误继续阻断、ACL 回读、错误账户和不足访问掩码。服务控制与审计设置需要 Windows 管理员权限；源码测试通过不等于真实隔离已经验证。

## 本轮验证

- 扩展后端回归：269 通过、3 跳过，见本地 `.pytest-harness-next.log`。跳过项为 2 项需提升令牌的服务测试和 1 项受目录链接权限限制的测试。
- Ruff 检查通过；552 个 Python 文件格式检查通过。
- 未修改前端；本轮未重跑前端构建与浏览器测试。
- 新执行版本指纹及源码归档见 [源码基线](../benchmarks/output/next-source-baseline.json)。原始首轮报告与归档保留。

## 真实模型复测

使用原 Provider、固定模型版本与原 ¥10 共享账本，对 `normalize-config` 运行 2 次重复 × 3 组，共 6 次 trial：[JSON](../benchmarks/output/volcengine-regression-next.json)、[Markdown](../benchmarks/output/volcengine-regression-next.md)、[JSONL](../benchmarks/output/volcengine-regression-next.trials.jsonl)。实验 ID 为 `da2e09e09b71492dae44ae481d7c6eec`，源码指纹与上述归档一致。

三组均为 2/2 通过。每个 trial 有 4 次真实 HTTP 请求，24 次请求均返回用量并关联到 Provider request ID；累计 Token 统计完整。repair 组均为首次通过，真实反馈修复仍未触发，本次结果证明统计与关联链路，不证明修复收益。

本次新增保守估算 ¥0.236258。共享账本累计 66 次 HTTP 请求，已知用量保守估算 ¥0.672614、原未知请求预留 ¥2.113536、合计 ¥2.786150，剩余预算 ¥7.213850。未核对实际账单，没有清零或释放旧预留。

## 尚待真实环境验证

Broker 控制面曾达到 running / healthy，并通过真实探针定位了上述两个初始化问题。最新服务状态为 stopped；当前进程未提升，服务启动请求被 Windows 拒绝（错误 5），系统启动器尝试也未能恢复运行。管理员重启之后仍需重新运行探针，不能把源码修复和单元测试写成隔离验证通过。

因此本轮代码题付费评测及命令运行中进程终止恢复尚未执行。代码评测入口在准入前阻断，文件任务复测未使用命令工具。需要先在管理员 PowerShell 启动 `PraxisSandboxBroker` 并确认 running，再检查三项真实文件权限探针；若有后续错误，继续定位并保留阻断，不做宿主执行兜底。

## 管理员启动后的继续验证

用户确认服务 Running 后，已继续真实命令诊断，并修复三个问题：

1. 后端只读检查父目录身份，取消对 `C:\Users` 等受保护父目录的不必要 ACL 写入。现有 Broker 令牌保留 `SeChangeNotifyPrivilege`；该权限只允许目录穿越，不提供目录列表或文件读写权限，见 [微软说明](https://learn.microsoft.com/en-us/windows/security/threat-protection/security-policy-settings/bypass-traverse-checking)。工作区、临时目录的授权和身份检查继续保留。
2. 预检通过现有资源队列取得命令 permit，结束后取消等待并核对清理结果，不绕过 Broker 的资源准入。
3. 沙箱新建对象的默认 DACL 加入安装时受保护配置中的 controller SID，使本地后端在命令结束后仍能读取和清理正常输出。该 SID 来自 Broker 安装配置，不接受模型指定；未加入 Everyone，也未改变沙箱令牌的 restricting SID 集合。

三项真实写权限探针曾通过，随后参考解运行暴露 Python `__pycache__` 的读取/清理权限问题。预检现增加后端读取沙箱输出的检查，避免只检查文件存在就放行。默认 DACL 修复需要 Broker 重启加载；当前增强探针准确记录输出读取被拒绝，代码题继续在付费前阻断。

最新扩展回归为 276 通过、3 跳过，见 `.pytest-code-admission-final.log`；Ruff 检查和 552 个 Python 文件格式检查通过。此处记录源码验证，最终真实输出读取与代码评测结果待重启后追加；目前未新增付费模型请求。
