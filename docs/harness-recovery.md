# Harness 恢复与实验预检

Praxis 仍是本地单用户应用。恢复只读取本机 SQLite 和已批准工作目录，不引入账户、云端任务服务或新的数据库。

## 恢复流程

后端首次访问会话时识别上个进程遗留的 running Turn。能够匹配持久化 Runtime 的任务变为 paused，保留原工作流、检查点、累计用量和待定工具；没有对应 Runtime 的孤立 Turn 仍记录失败。系统不会在启动时自动继续任务。

浏览器点击“继续”后先获取 `GET /api/turns/{turn_id}/recovery?session_id=...`，展示目录、检查点、尝试编号、预算、结果不确定的工具和文件变化。用户可返回检查，或确认继续。`POST /api/turns/{turn_id}/resume` 必须携带预览中的 `recovery_token`，不接受替换验收合同。向 paused 任务追加消息（包括队列消息）时，`POST /api/turns` 也必须携带该指纹并经过同样的检查，继承原合同。

接口在接纳前比较预览指纹；执行前再次检查。预览过期返回冲突或停止本次恢复，不执行模型/工具。指纹绑定原 run、Thread、版本、目录、检查点、累计用量、验收进度和当前受监测文件。它用于检测过期观察，不是认证凭据，也不能锁住用户正在编辑的文件。

恢复创建同一 workflow 的下一个 attempt，保留模型轮数、工具用量、修复次数和历轮报告。新配置只能收紧该工作流的预算。新任务使用当时的配置重新开始预算。

已开始但没有可靠结果的工具标记为 `indeterminate`、`replay_safe=false`，系统不自动重放原调用。模型获得恢复检查摘要，再自行检查当前状态；后续新工具调用沿用原审批。这个保证不等于任意外部副作用的 exactly-once：模型仍可能提出语义相似的新调用，命令或外部 MCP 的最终影响需要用户核对。

## 文件观察范围

检查点只保存文件指纹，不保存文件内容。观察验收规则、`read_file` 和 `file_operation` 明确涉及的路径，使用既有 `ScopedPaths` 拒绝越界、符号链接和 reparse point。最多 64 个文件，每个最多 256 KiB；大文件、目录、无权读取的路径和超出数量的情况显示为未检查。

命令隐式修改、外部服务、未声明文件和大文件内容不在这项检测保证内。空基线或旧检查点会明确提示无法判断外部修改。两次检查之间和最后检查之后仍存在用户或其他进程修改文件的时间窗口，不宣称整个工作区快照隔离。

验收开始、检查完成和分配下一次修复都成为持久化检查点，在向前端通知前保存。这样，在修复事件边界终止进程也不会把已消耗次数重置。

## 本地预检

```powershell
uv run python -m benchmarks.preflight
uv run python -m benchmarks.preflight --sandbox-smoke
uv run python -m benchmarks.recovery
```

默认预检只记录源码/锁文件指纹、Git 状态、Python、Broker 健康和实验规模，不读取 Provider 密钥，不调用模型。`--sandbox-smoke` 显式通过真实 Broker 在独立临时夹具中验证工作区写入、只读拒写和工作区外拒写；使用正常的 Broker 准入和审计流程，不安装/修复服务或关闭审计，不是网络及全面隔离认证。

`benchmarks.recovery` 用父进程真正终止独立 worker，在重新打开 SQLite 后恢复四个场景：模型等待、文件副作用发生但结果未提交、修复事件边界、用户编辑文件。模型等待场景调用一个 loopback HTTP 夹具，在它收到请求但尚未回应时终止进程，并核对请求次数已持久化；其余规划输出使用本地脚本。没有外部模型请求或费用。命令运行中中断必须等真实 Broker 准入验证通过后另测，目前单独标记未运行。

## 真实模型实验入口

当前火山引擎 Provider 已完成接入与三题试跑，使用本地加密凭据、共享 ¥10 预算账本的入口及真实结果见 [接入记录](volcengine-integration.md)。下方通用 TOML 入口不包含该费用保护。

先选择四类任务各一题，三组各一次，共 12 次：

```powershell
uv run python -m benchmarks.harness --task-id normalize-config --task-id write-report --task-id repair-slugify --task-id release-version --repeat 1 --output benchmarks/output/harness-pilot-demo.json
```

上面省略 `--config`，只运行免费脚本演示。只有明确提供服务、固定模型版本和本轮费用上限后，才将 `--config` 指向已有模型 TOML 执行真实试跑。模型/工具次数上限不等于货币费用硬上限，须同时配置服务侧限额和输出 Token 限制；程序不凭空推算价格。

试跑检查原始请求/返回模型、Token 缺失、独立评分、失败分类和预算消耗，再决定是否运行 12 题 × 三组 × 三次 = 108 次的完整实验。真实实验可以得到无提升或负提升结果。

报告使用同一 `experiment_id` 关联 JSON、逐次 JSONL 和各 trial，保存选题、完整套件哈希、公开提示哈希、源码内容哈希、锁文件哈希和安全的模型配置摘要。指纹范围由 `benchmarks/provenance.py` 中的目录及后缀白名单定义，包含未提交源码；不记录远端地址、环境变量、API Key、完整配置或工作目录内容。

本工作目录原来没有 `.git`，现已初始化本地 Git，尚无 commit 或 remote。`git_head=null` 如实保留，不能仅凭 HEAD 重建本轮源码；需要随报告保存对应源码包并校验 `source_sha256`。本轮未创建提交或推送。

本轮已保存并逐项校验白名单源码归档，清单、归档位置和验证结果见 [第三阶段记录](harness-stage3-validation.md)。
