# SCORP V4：给普通网页版 GPT 的交接说明

这份说明解决一个常见误会：**普通网页版 GPT 读到 Git 仓库，不等于它已经接入了本地 Windows。** Git 只能让它看到代码、计划和证据；本地 SQLite、Python、Chrome Use、Windows MCP 和浏览器登录状态仍然属于本机。没有本地宿主或连接器时，网页版 GPT 必须停在 `WEB_GPT_DIRECT_LOCAL_CONTROL_UNAVAILABLE`。

当前候选版本是 GPT-5.6 Sol 兼容路径，代码候选提交为 `5c4a127f03642778799c65abdf04aa1fbdddcb9e`。该身份与 `docs/handoffs/SCORP_V4_GIT6_VALIDATION.json`、`SCORP_V4_GIT6_CANDIDATE_MANIFEST_5c4a127.json` 和 `SCORP_V4_GIT6_PREFLIGHT_5c4a127.json` 一致。后续提交只增加交接文档和证据，不改变这份代码候选。交接文件定义的是连接边界和使用顺序，不把规划文档当成运行证据。

## 先判断你现在是哪一种模式

| 模式 | 网页 GPT 能做什么 | 不能做什么 |
|---|---|---|
| 只有网页聊天 | 读 Git、分析目标、生成结构化计划、审查证据 | 读本地 SQLite、运行 Windows 命令、打开 Chrome、创建 GPT 会话 |
| 有 Windows 操作员 | 操作员把预检 JSON 和运行证据交回网页 GPT | 网页 GPT 仍不能假设自己直接控制本机 |
| 有本地 SQLite 监控 | 监控已有 Master 租约并写决策日志 | 监控入口不发送浏览器提示词，也不绕过登录 |
| 已配置浏览器适配器 | 在显式门禁下运行 Master A 和 Worker | 登录失效、验证码、提交结果不明确时必须暂停 |

## Windows 操作员从零开始

在 Git 仓库根目录打开 PowerShell，先执行只读预检：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B .\scorp-agent\chatgpt-gui-bridge\tools\v4_local_preflight.py `
  --repo-root $PWD `
  --database-path C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --allowed-root C:\ScorpAgent\workspaces\project
```

预检会分别报告：

- `offline_validation=READY`：仓库和 Python 可以做离线验证；
- `sqlite_master_monitor=READY`：指定 SQLite 和允许目录已经存在；
- `browser_rebind=READY_TO_REBIND`：已提供 Chrome Use 可执行文件和驱动状态；
- `web_gpt_direct_local_control=UNAVAILABLE`：这是正常的边界，不是程序故障；
- `browser_submission=EXPLICIT_OPERATOR_GATE`：预检本身永远不发送消息。

如果接手者完全不知道怎样读取这些边界，先生成一个可以直接上传到普通网页版 GPT 的交接包。它只读取仓库、候选证据和本地配置；除写入你指定的 JSON 文件外，不启动浏览器、不发送消息、不执行项目任务：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B .\scorp-agent\chatgpt-gui-bridge\tools\v4_web_gpt_packet.py `
  --repo-root $PWD `
  --database-path C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --allowed-root C:\ScorpAgent\workspaces\project `
  --output .\docs\handoffs\SCORP_V4_WEB_GPT_PACKET.json
```

把 `SCORP_V4_WEB_GPT_PACKET.json` 上传给普通网页版 GPT，并把 JSON 中的 `prompt` 字段作为开场指令。退出码 `0` 表示交接包内部一致且预检没有阻塞；退出码 `2` 表示必须先处理包内的 `blockers`。即使退出码为 `0`，包仍会明确写出 `WEB_GPT_DIRECT_LOCAL_CONTROL_UNAVAILABLE`：它是网页 GPT 的规划/审查输入，不是本地权限授予。

先确认离线候选：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\run-candidate-validation.ps1
```

如果只需要监控已经存在的 Master 租约，可以运行一次监控探针：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
python -B .\scorp-agent\chatgpt-gui-bridge\tools\v4_master_supervisor_runtime.py `
  --database-path C:\ScorpAgent\v4-runtime\state.sqlite3 `
  --allowed-root C:\ScorpAgent\workspaces\project `
  --decision-log C:\ScorpAgent\v4-runtime\supervisor.jsonl `
  --max-iterations 1
```

这个入口只接管已有 SQLite 状态，不会凭空创建根合同或项目；没有活动租约时会报告阻塞。`--forever` 只应由明确负责生命周期的 Windows 宿主使用。`--rebind` 只启用已登录会话的只读快照和绑定核对，不创建新聊天、不填入提示词、不点击发送。

真正需要浏览器发送时，必须准备结构化计划 JSON，并在审查路径、允许目录、浏览器状态和发送内容后，才使用 `v4_master_controller_runtime.py --send`。`--send` 是人工明确门禁，不是普通网页 GPT 自动获得的权限。登录失效、验证码或提交结果含糊时，运行必须停在 `BLOCKED`。

## 网页 GPT 应该收到什么

把下面的消息发给负责规划的普通网页版 GPT，同时附上预检 JSON 和后续运行证据：

```text
你是 SCORP V4 的规划与验收端，不是本地 Windows 执行进程。

先读取：
1. GPT_START_HERE.md
2. docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md
3. docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json

当前约束：
- 模型路径是 GPT-5.6 Sol；不要把仓库名 6 解释为 GPT-6。
- 不使用付费模型 API，不索取或写入 API key、Cookie、验证码或登录数据。
- Git 只传递计划和证据；SQLite 才是本地运行时的权威状态。
- 没有本地预检和当前运行证据时，你只能输出计划或 BLOCKED，不能声称已经控制电脑、打开浏览器、创建 Worker 会话或完成项目。

工作顺序：
1. 判断预检报告中的 capability 状态。
2. 输出一个包含 root_contract、acceptance_contract、plan 的结构化计划。
3. 为每个任务指定依赖、允许路径、允许动作和验收 ID。
4. 等待本地宿主返回命令、退出码、证据引用和候选提交哈希。
5. 独立区分 PASS、FAIL、BLOCKED、NOT_RUN；任何缺少证据的项目都不能算完成。
6. 如果需要浏览器登录、验证码、Windows 交互会话或人工发送确认，明确报告阻塞并停止。
```

## Git 交接怎样工作

如果没有可调用的本地连接器，Git 交接是“网页 GPT 产出计划，Windows 宿主执行，网页 GPT 审查证据”的人工或外部轮询流程：

1. 网页 GPT 生成计划副本，不改写规范模板和生产状态。
2. Windows 宿主在本地验证计划、路径和候选提交，再调用 SQLite 控制面。
3. 宿主把 JSON 运行结果、证据哈希、候选提交和阻塞原因提交回 Git。
4. 网页 GPT 读取这些文件，给出下一步或最终验收判断。

仓库里保留的旧 `relay.ps1` 是 V3 JSON relay 的兼容路径；它不能被当成当前 V4 SQLite 控制面已经接通 GitHub 的证明。当前 V4 的 GitHub 角色是请求和证据发布入口，权威状态仍在本地 SQLite。

## 交接完成的判据

交接本身只有在接手者能回答下面四个问题时才算清楚：

1. 网页 GPT 当前是否有本地连接？如果没有，是否明确为 `UNAVAILABLE`？
2. Windows 上实际启动了哪个入口、使用哪个候选提交？
3. SQLite、浏览器会话和 Git 之间谁是权威？
4. 当前结果是 `PASS`、`FAIL`、`BLOCKED` 还是 `NOT_RUN`，证据文件在哪里？

任何一个问题答不上来，都应回到预检，不应继续重试浏览器或把规划文字当成完成报告。
