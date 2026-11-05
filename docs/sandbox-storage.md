# 沙箱执行数据存放到其他磁盘

本机已配置：

| 数据 | 位置 |
| --- | --- |
| 新建会话工作区及上传文件 | `F:\PraxisSandbox\workspaces\<session_id>\workspace` |
| 每次命令的临时目录、子进程 TEMP/TMP/HOME | `F:\PraxisSandbox\tmp\job-*` |
| 会话数据库、设置、加密模型凭据 | 原 `~/.praxis` |
| Broker 服务安装信息和 Windows 账户 | 原 Windows 服务和 `%ProgramData%/Praxis/SandboxBroker` |

执行目录与小体积控制数据分别存放。配置不改变系统全局 TEMP/TMP，不移动服务身份或密钥，也不放宽 ACL、只读权限和磁盘保护。

## 配置

在实际使用的 `~/.praxis/config.toml` 的 `[sandbox]` 表中设置：

```toml
[sandbox]
policy_version = 4
workspace_root = "F:/PraxisSandbox/workspaces"
temp_root = "F:/PraxisSandbox/tmp"
```

保留该表已有的网络和资源设置，不用整个示例覆盖原配置。两项都是可选的绝对本地目录路径；空字符串恢复默认的新目录选择。相对路径、文件路径、符号链接和重解析点会被拒绝。

改变 `workspace_root` 后重启后端。`temp_root` 会在新构建的任务启动器中生效。本次配置时后端未运行，下一次正常启动即可使用：

```powershell
uv run python -m backend.api
```

无需重新安装或迁移 Broker 服务。F 盘目录不可访问、权限不足或空间未达到保护阈值时会报错，不自动退回 C 盘或宿主执行。

## 会话与文件边界

新会话的数据库仍在 `~/.praxis/runtime/<session_id>/state.db`。外部工作区位置以 `workspace-location.json` 记录在相同会话元数据目录，重启、复制会话和文件接口使用同一位置。后续变更配置不会把已有会话改指向空目录。

原先已存在的会话工作区保持原位；本轮配置前本机没有会话工作区，因此没有需要迁移的已有会话文件。项目目录也保持用户选择的实际位置：若项目本身位于 C 盘，对该项目的磁盘空间检查仍然有效。

复制会话会复制工作区内容而不复用来源位置；永久删除会话会清理该会话的外部工作区及元数据，保留其他会话和存储根目录。已配置目录的链接和路径边界检查在解析、创建和清理时继续生效。

本轮仅创建 F 盘目录并更新非敏感 TOML 设置，API Key 保持不变。修改前的配置备份位于 `benchmarks/output/sandbox-fdrive-config-before.toml`。旧临时文件、历史实验与其他应用目录没有被清理。

## 实际验证

[真实验证结果](../benchmarks/output/sandbox-fdrive-verified.json)使用应用的真实 `WebAppState`、配置解析、会话路径和 Sandbox 启动器：

- 子进程的工作目录、临时目录和 HOME 均在 F 盘。
- 工作区产物写入、读回通过。
- 只读模式写入被拒绝，已有内容保持原样。
- 自有命令资源清理完成；仅删除此次新建的验证会话。
- 外部模型请求为 0。

相关路径、配置、文件接口、原生沙箱回归 **140 通过、3 跳过**，另一次会话生命周期、复制、删除与恢复相关回归 **125 通过、1 跳过**；两轮有重叠，不合计为新增测试数。跳过项涉及系统链接权限。前端类型检查、Ruff 检查及 570 个 Python 文件格式检查通过。没有新增模型请求。
