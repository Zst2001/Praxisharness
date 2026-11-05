# Broker 恢复与真实代码评测

日期：2026-11-04（Asia/Shanghai）。本轮继续处理管理员停止服务报错及代码题准入，已恢复 Broker，并完成真实模型代码题三组试跑。

## 服务停止与文件权限

用户的 `Stop-Service` 报错后，SCM 实际状态已变为 stopped、进程 ID 为 0，Win32 与服务退出码均为 0。因此停止最终已发生；本轮没有再次要求停止同一服务，也没有结束无关进程或修改服务 DACL。

源码停止路径存在同步管道等待和资源清理阻塞控制回调的问题。现在 `SvcStop` 先报告停止中并发出停止信号；接收、读取和写入使用可取消的 overlapped I/O，实际资源清理在服务线程完成。每个 I/O 由发起它的线程调用 `CancelIo` 取消，等待完成后再释放 OVERLAPPED、事件和管道，适配当前 pywin32 不提供 `CancelIoEx` 的情况。状态探针也使用同线程取消，避免超时后留下未完成操作。微软相关说明：[管道异步 I/O](https://learn.microsoft.com/en-us/windows/desktop/ipc/synchronous-and-overlapped-input-and-output)、[CancelIo](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelio)。

新测试使用真实 Windows 临时命名管道，覆盖空闲监听、1 个和 20 个静默客户端、24 个并发请求、状态读取超时取消，以及停止回调不执行资源清理。6 项通过。本轮没有对生产服务执行额外的停止循环；恢复启动后继续验证命令运行。

输出文件权限同时保留两层控制用户授权：令牌默认 DACL，以及仅在明确工作区和临时目录上持久保留的控制用户继承 ACE。后者避免命令租约撤销后，新建文件及 `__pycache__` 丢失控制用户访问权限。父目录只检查身份，不修改 `C:\Users` 等受保护目录。未向 Everyone 增加权限，也未改变沙箱 restricting SID 集合。文件继承依据见 [微软文件安全说明](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)。

[最终真实预检](../benchmarks/output/preflight-output-inheritance-fixed.json)中，工作区写入及后端读取、只读拒写、工作区外拒写均通过，清理完成。该结果覆盖这些文件权限探针，不是网络或完整隔离认证。

## 参考解验证

[真实 Broker 参考解报告](../benchmarks/output/challenge-real-broker-reference-complete.json)使用脚本规划器，实际在 Broker 中运行公开测试和独立 Python 评分。baseline 与 verify 按脚本设计保留错误产物；repair 经一轮失败反馈写入参考解后通过，临时工作区清理成功。该报告是机制验证，不作为真实模型成功率或提升数据。

## 真实模型结果

使用固定版本 `deepseek-v4-1-flash-261017`，选择 `merge-layers`，2 次重复 × baseline / verify / repair，共 6 次 trial。三组共同上限为 16 个模型决策、12 次工具调用；repair 最多 2 次修复。所有命令与独立评分都通过真实 Broker，验收合同和评分未在运行中修改。

| 组别 | 完成且独立评分通过 | 其他结果 |
| --- | --- | --- |
| baseline | 0 / 2 | 2 次工具预算耗尽，其中 1 次产物独立评分已通过，但执行未完成 |
| verify | 1 / 2 | 1 次公开验收通过、独立边界评分失败 |
| repair | 0 / 2 | 2 次公开验收通过、独立边界评分失败 |

没有环境阻塞、模型请求错误或新增超时。53 次实际 HTTP 请求均返回用量；全部 trial 的累计用量完整。三次“公开验收通过但独立评分失败”是实际误接受记录。repair 组没有触发验收后的反馈修复，因此本轮没有真实修复收益证据；不能根据这 6 次结果声称某组更优。

本轮暴露的优先改进点是公开测试对深层合并与输入不可变性的覆盖，以及固定工具预算下的执行效率。独立评分只给出行为检查失败，当前报告未保存候选源码，不能反推失败的具体代码行。后续应保留脱敏后的候选产物、工具轨迹和逐边界检查结果，再完善验收条件；不将隐藏评分直接反馈给本轮模型。

原始结果：[完整 JSON](../benchmarks/output/volcengine-challenge-next.json)、[Markdown](../benchmarks/output/volcengine-challenge-next.md)、[逐 trial JSONL](../benchmarks/output/volcengine-challenge-next.trials.jsonl)。实验 ID 为 `0e19da8db17a4997a64a5eee8167e8fd`，源码指纹为 `551d06d4b1f24f13439617ec6b42320532bc352a3dcf361ddb9cb2d9028abc75`，执行版本及逐文件清单见 [源码归档基线](../benchmarks/output/broker-source-baseline.json)。

## 费用与回归

本次新增保守估算 ¥0.947584，沿用原 ¥10 账本。全部真实试跑累计 119 次 HTTP 请求，已知用量保守估算 ¥1.620198，原未知超时预留 ¥2.113536，合计 **¥3.733734**，剩余预算 ¥6.266266。没有新建账本或释放旧预留；这些是估算和预留，未核对实际账单。

本轮扩展回归 283 通过、3 跳过，见 `.pytest-broker-complete.log`；跳过项仍为 2 项提升令牌测试和 1 项目录链接权限测试。Ruff 检查及 553 个 Python 文件格式检查通过。未修改前端，没有提交或推送代码。

命令运行中终止后恢复仍待单独验证；已有四种进程终止场景不因此自动扩大为该场景已通过。

后续已补齐候选源码证据、逐 case 失败类型、零模型调用重评分，并完成 `merge-layers` 公开验收 V1/V2 各三组真实对照；新结果单独记录于 [证据与 V2 验证](harness-evidence-validation.md)，本页历史试验不回填或改写。
