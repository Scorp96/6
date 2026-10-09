# SCORP — 下一任 GPT 完整交接与 Windows 控制手册
日期：2026-10-09 UTC+8。性质：隔离研发/只读现场交接；不授予浏览器发送或生产切换权限。

## 一、下任 GPT 第一条指令（可整段复制）
请通过我的 GitHub 连接器接手 SCORP。先打开仓库 Scorp96/6 的 AGENTS.md、GPT_START_HERE.md、本文件 docs/handoffs/SCORP_NEXT_GPT_FULL_HANDOFF_20261009.md、docs/handoffs/SCORP_GPT_SESSION_COMPATIBILITY_AUDIT_20261009.md；读取隔离分支 experiment/gpt-session-model-evidence-20261009、Draft PR #1 的最新 commit 和准确对应的 GitHub Actions CI。查私有 Scorp96/scorp-control-plane 最新的 SCORP_EXEC 执行回执，先新建唯一编号的只读握手 Issue，得到本机 CLAIMED→RESULT 才能声称控制了 PC。不重播旧 MASTER_REASONING，不清空 BLOCKED_AMBIGUOUS，不改原 Master URL 或 GUI Watchdog；不修改 C:\ScorpAgent\experiments\r2-gpt-session-audit-20261009 的旧固定 SHA，不启用未经验证的浏览器发送。继续通过隔离 Git 分支和独立本机实验目录推进，并给出准确的新 SHA、测试、实机结果、阻塞和下一步。注意当前我是 GPT-6，仓库根目录仍规定 GPT-5.6 Sol；不能虚假冒充旧模型，也不能静默绕开生产运营规则。

## 二、实际架构
用户 ChatGPT 网页会话 GPT1..GPTN → 逻辑 Master A + 动态 Worker → V4 SQLite/任务图/并发槽 → 本机 Chrome Use GUI Bridge → 可审核的浏览器 submit 意图 → 主机采集回复 → evidence/acceptance。GPT1..N 指多个会话而非模型名字，现已验收的 V4 Worker 并发最多 2。

GPT 不能直接通过阅读仓库控制 PC。已核实的控制路径：
~~~
新 GPT GitHub 连接器
 → Scorp96/scorp-control-plane 私有 Issues [SCORP_EXEC]
 → Windows SCORP Agent 认领 Issue
 → 本地有限 Powershell / Python / SQLite 只读任务
 → 原 Issue 下 SCORP_EXEC_CLAIMED、SCORP_EXEC_RESULT
 → GPT 核验 Status / ExitCode / sha256 / 脱敏 STDOUT
~~~
根目录 AGENTS.md 写默认队列 Scorp96/666，本轮实机确证的旧控制队列却是 Scorp96/scorp-control-plane。必须先核对哪个队列被真实消费，不可双向投递。GitHub 不等于直接远程桌面；Agent 不在线就不能执行。

## 三、仓库、分支和文件
主仓库：https://github.com/Scorp96/6
隔离分支：experiment/gpt-session-model-evidence-20261009
Draft PR：https://github.com/Scorp96/6/pull/1
PR 基线：fix/scorp-v4-r2-runtime-keepalive-20261001 / 0f1db544ee2afda86f0b4b24ba946e5b11。
此前严格验收成功的安全候选 SHA：b090e971258e45df4c5ba6ea075691f4357e7e6e；GitHub CI 37862277206：497 V4 + 625 GUI + 29 broker = 1151 PASS。之后新增的下一 GPT 只读诊断程序必须以**新 HEAD 的 CI**重新验收；不能引用老 SHA 的成功替代。
关键文件：
- scorp-agent/master_a_dynamic_v4/master_controller.py 逻辑 Master A。
- .../state_store.py、schema.sql、scheduler.py、activation_arbiter.py 是 SQLite/租约/任务权威。
- .../browser_adapter.py 是 MAY_HAVE_SUBMITTED / BLOCKED_AMBIGUOUS 意图屏障。
- .../session_admission.py 是物理身份和模型可选门禁。
- .../physical_progress_probe.py 页面只读进度观察；按钮消失不代表完成。
- .../turn_completion_evidence.py、host_terminal_receipt.py HMAC 只验证宿主收据；**当前没有生产真实终止事件签发器**。
- .../verified_continuation.py 只产生 no-send 候选，要求显式已入队去重清单；不授权发送。
- .../local_tick_observer.py 是隔离零 Token 到期观察器。
- .../run_isolated_observer_canary.ps1 和 install_isolated_observer_canary.ps1 是真实已安装的 15m 计划任务逻辑。
- .../next_gpt_status_probe.py 是本轮新增的下一 GPT 统一只读预检。
- scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py 是 Chrome Use 浏览器桥，不要擅自调用 send。

## 四、Windows 路径与硬隔离
生产 R1 权威库：C:\ScorpAgent\runtime-v4\active\state.sqlite3（仅 URI mode=ro、PRAGMA query_only=ON）
R1 Daemon 健康：C:\ScorpAgent\runtime-v4\active\daemon-health.json
V3 浏览器绑定：C:\ScorpAgent\state-v3\active\chrome-use-driver-v3.json
GUI Bridge 健康：C:\ScorpAgent\chatgpt-gui-bridge-state\health.json
Python：C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe
Chrome Use CLI：C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe
活跃 15m 观察器代码目录：C:\ScorpAgent\experiments\r2-gpt-session-audit-20261009
活跃 15m 观察器**固定 SHA**：8da5085f906b4aeeece6fb0ae1f488bc597507b1。禁止在这个目录 checkout/reset 或修改任何 Python/PowerShell 文件。
观察器数据库：C:\ScorpAgent\experiments\r2-observer-state-20261009\scorp-readonly-observer.sqlite3
新的独立安全实验目录：C:\ScorpAgent\experiments\r2-host-terminal-security-20261009
本机回执目录：C:\ScorpAgent\state-v4\issue-<number>-action-<id>-result.json，真实位置以回执为准；不可上传可能有敏感内容的 raw 输出。
Windows Task Scheduler：ScorpR2GPTObserver15mCanary 已启用，5m 调度一次，SQLite 门禁不早于 15m，到期实际可能约 15–20m；只读、不发送。25m Task 未安装。确需紧急停用、并核对了准确身份时可用：Disable-ScheduledTask -TaskName "ScorpR2GPTObserver15mCanary"；不得默认停用。

## 五、怎么由 GPT 真实控制电脑：GitHub Issue 协议
1. 新 GPT 用户授权安装/连接 GitHub，确认仓库访问；用 mcp__GitHub__fetch_file 读代码，用 mcp__GitHub__fetch 读 PR/CI。
2. 用 mcp__GitHub__create_issue，在 Scorp96/scorp-control-plane 新建标题以 [SCORP_EXEC] 开头的 Issue；正文是以下 JSON 对象，task_id 和 action_id 必须全新唯一。
~~~json
{
 "protocol_version":"scorp.exec/v4",
 "task_id":"new-gpt-readonly-UNIQUE_ID",
 "action_id":"new-gpt-readonly-UNIQUE_ID",
 "action_kind":"powershell",
 "working_directory":"C:\\ScorpAgent",
 "timeout_seconds":45,
 "safety_class":"standard",
 "authorization":"user-authorized-readonly-audit",
 "expected_preconditions":{"read_only":true,"no_browser_submit":true},
 "payload":{"script":"$ErrorActionPreference='Stop'; Write-Output 'SCORP_READONLY_AGENT_REACHABLE'"},
 "evidence_requirements":["stdout_tail","stderr_tail"],
 "continuation_hint":"Read-only handshake; no browser, no Task Scheduler modifications."
}
~~~
3. 用 mcp__GitHub__fetch_issue_comments，传参 repo_full_name: Scorp96/scorp-control-plane 与 create_issue 返回的 issue_number。
4. 出现 SCORP_EXEC_CLAIMED 只算“认领”；只有 SCORP_EXEC_RESULT 的 Status: SUCCEEDED + ExitCode: 0 + 输出合约匹配才算执行成功。没有回执、超时、FAILED/PARTIAL 不算成功。
5. 不因失败重复提交可能带副作用的相同 Issue。浏览器发送边界不明时必须只读对账；不能重发。
6. 创建文件用 mcp__GitHub__create_file，改已有文件用 update_file 的原 sha；GitHub 改文件和 Windows 本机命令是不同写入。
7. 新 GPT 不可输出 ClaimToken、真实用户名、cookies、session token、对话正文或 remote identity。

统一接管程序位置：scorp-agent/master_a_dynamic_v4/next_gpt_status_probe.py；仅在**另外的独立新 HEAD 已通过 CI/本机测试**的检出中运行以下单行 PowerShell，不可在活跃旧目录更新：
~~~powershell
$env:PYTHONPATH='C:\ScorpAgent\experiments\r2-host-terminal-security-20261009\scorp-agent'; & 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe' -B -m master_a_dynamic_v4.next_gpt_status_probe --scorp-root 'C:\ScorpAgent'
~~~
其固定 JSON 输出只包括 R1、观察器、GUI/V3 状态、blockers，不提供浏览器发送权；文件不存在/损坏时必须报告 MISSING/UNREADABLE，而不是创建数据库。

## 六、最近经过实机核验的状态
最新只读实机 Issue #2316，2026-10-09 08:19 UTC+8：
- 15m 观察器 SQLite：完整性 ok；事件数 5；最新 seq 5 在 08:17:47；reason MASTER_ROTATION_CONFLICT_UNRESOLVED。
- R1：ACTIVE/BOOTSTRAP/state_version=0；原 BLOCKED_AMBIGUOUS 1 条；browser_bindings=0。
- Daemon：epoch=44，ACTIVE lease；曾发生 LEASE_EXPIRED 和自动恢复。
- GUI Bridge：ERROR / MASTER_CONVERSATION_ROTATION_REQUIRED。
以上是一次快照，不是持续保证。新 GPT 必须查新 Issue。
原 Master 事实：#2311 原 MASTER_REASONING 一次，Outbox BLOCKED，remote identity/response SHA 缺失，最后原因 CONVERSATION_URL_INVALID；#2312 原不可变事件有 BROWSER_SUBMIT_EXCEPTION；#2313 异常类型 TimeoutError。不能因 URL 无效倒推旧消息从未发出，禁止重发。
Daemon：#2314 过去 24h FAILURE 2、RECOVERY 2；#2315 lease ACTIVE、epoch 44、30s TTL、历史 recovery_count 41；没有连续 24h 零故障验收。
Streaming：#2304 enabled/connected；#2306/#2307 仅本机 loopback listener。Chrome Use WebSocket 是**双向浏览器控制**，不是可信 GPT final 事件。

## 七、已成功的历史控制 Issue（可直接查真实回执）
- #2283/#2284：Windows 原生 health 与有界 Task Scheduler 只读探针。
- #2285/#2286：15m 安装前 21 项测试、Inspect/DryRun。
- #2287/#2288：仅隔离 15m 任务先 Disabled 注册、再 Enable。
- #2289/#2294/#2297：Windows 真实自动触发，5m 不到期去重。
- #2298/#2314/#2316：15m 到期完整观察持续写入隔离 SQLite。
- #2301/#2308/#2309：独立安全检出本机 49/51/497 单元测试通过。
- #2305/#2306/#2307：Chrome Use stream 双向接口及本地监听。
- #2311/#2312/#2313：原 Master 异常原始证据与 TimeoutError。
- #2315：R1 Daemon 租约及恢复审计。
Issue 格式：https://github.com/Scorp96/scorp-control-plane/issues/2316

## 八、下一阶段分级任务
P0 安全对账：不清空原 BLOCKED_AMBIGUOUS；只有可信远端原提交证据或明确非提交证据才能放行相应状态变化。
P0 真实 GPT final：HMAC 验证只是数据完整性，尚缺真实宿主终止事件生产者；必须独立安全设计和本机钥匙保管、跨重启 replay ledger，新的隔离测试 GPT 会话先验收，原 Master 不发送。
P1 GPT 自行续作：待 Master/Worker 物理绑定、不可变意图、准确回复完成、去重 ledger 和独立用户授权全部通过才做隔离端到端 canary；2 Worker 并发不自动扩容。
P1 Daemon 稳定性：只读定位 LEASE_EXPIRED 的睡眠、服务停止、Windows 日志或心跳延迟；真正连续 24h 才可 PASS。
P2 15/25m 精度：当前 5m poll 带来观察时间偏移；精确调度先独立试验，不覆盖旧运行任务；25m 自动计划任务 NOT_RUN。

## 九、代码修改与验收规范
- 先查 AGENTS.md、GPT_START_HERE.md 与私有 coordination/coordination-state.json、coordination/SCORP_COORDINATION_V1.md；检查排他写锁，过期协作 JSON 不等于 R1 权威 SQLite。
- 仅在隔离分支提交、尽量小改；不把当前活跃 15m 观察器目录更新到新 HEAD。
- 最新 CI 必须是 PR 当前完全一致 SHA 的 completed/success；此前的 cancelled 或旧成功不能替代。
- 运行 V4 核心、GUI Bridge、Broker、编译和 scripts/run-candidate-validation.ps1；本机单独实验执行也须有 SCORP_EXEC_RESULT。
- 浏览器 REAL/NOT_RUN、生产变更 YES/NO、CI PASS/FAIL/BLOCKED 都分别汇报。
- 需要 browser send、生产 Master 轮换、原 R1 改库、改生产任务，必须另有明确权限和验收，不能凭这份交接默认授权。

## 十、中断交接的最低完整格式
~~~text
SCORP_NEXT_GPT_HANDOFF
Date UTC+8:
GitHub repo / branch / draft PR:
HEAD and base SHA:
Exact-head CI ID / result / test counts:
Current verified Windows Issue queue:
New Issue # / task_id / action_id / CLAIMED / RESULT / ExitCode:
Last live state/UTC:
Original R1 status/version/daemon epoch/lease:
Old ambiguous Master state and immutable evidence:
GUI Bridge status/rotation conflict:
V3/V4 physical bindings:
Pinned 15m observer SHA/task state/latest seq:
25m scheduled task:
Host terminal evidence issuer: VERIFIED/NOT_RUN/BLOCKED:
HMAC verification / replay ledger:
Real browser messages sent?:
Production modified?:
Actual isolated tests (counts):
New commits and paths:
Unfinished blockers:
NEXT SINGLE SAFE ACTION:
~~~
新 GPT 必须以新 GitHub/Windows 回执更新本报告，不能照抄历史“通过”。


## 十一、本次新增的隔离宿主完成事件 Replay Ledger

新源码：scorp-agent/master_a_dynamic_v4/isolated_host_replay_ledger.py
新测试：scorp-agent/master_a_dynamic_v4/tests/test_isolated_host_replay_ledger.py

这不是对原生产 R1 增加浏览器消息发送能力。它只在显式传入、**真实存在且非符号链接**的 experiments/r2-* 隔离目录，允许创建名为 host-terminal-replay-ledger.sqlite3 的独立库。先走原有两份 HMAC 宿主收据校验；核对同一物理会话/URL/代际/intent/响应摘要；通过后原子记录 receipt 的 event_id 与递增 sequence。相同 event 会变为 ALREADY_RESERVED；旧 sequence 被拒绝；不同会话有独立 highwater。返回的 browser_send_authorized 永远为 false，所谓 RESERVED_FOR_REVIEW 仅表示“本地审查用去重预留成功”。

**安全边界：** 当前 Chrome Use 没有真实可信的 GPT terminal-event 签发器，也没有证据证明 HMAC key 的生产隔离保管。因此不能把测试签名器制造的演示 receipt 写成真实 GPT 事件；本模块没有和现有 Task Scheduler、原 Master 提交适配器连接。若未完成本模块最新 SHA 的全部 CI/Windows 本机测试，状态应记 NOT_RUN，不能称 production accepted。

下一 GPT 必须分离验证：1) event 原产地受信任；2) HMAC 真正持有本机密钥；3) replay ledger 跨崩溃有效；4) 独立续作意图在发送前持久化；5) 单独用户授权的 browser-send adapter。缺任一条件都保持 BLOCKED。


## 十二、2026-10-09 08:32–08:33 新增 Windows 异常重启根因证据

只读取证：
- Issue #2317：https://github.com/Scorp96/scorp-control-plane/issues/2317
- Issue #2318：https://github.com/Scorp96/scorp-control-plane/issues/2318

核对原 V4 最近一次 LEASE_EXPIRED：2026-10-09 07:20:13 UTC+8。Windows System 日志在其前一分钟同时出现：
- Kernel-General ID 12 约 07:19:13（Windows 系统启动事件）。
- Kernel-Power ID 41 约 07:19:16（上一运行期异常关机后出现）。
- EventLog ID 6008 约 07:19:31（Windows 非正常关闭记录）。
- EventLog ID 6005 约 07:19:31（事件日志服务启动）。
- V4 在约 07:20:13 记录 LEASE_EXPIRED，随后恢复到新 epoch。

Kernel-Power 41 事件的安全数值字段为 BugcheckCode=0、PowerButtonTimestamp=0、SleepInProgress=0、BootAppStatus=0；附近未获得明确 BugCheck 1001 事件。**不能据此断言没有 BSOD**，但目前也没有找到明确的 bugcheck 代码。断电、强制复位、硬件/驱动故障仍要另行查证，不能把这次租约过期草率认定为 Daemon 逻辑缺陷。

**验收必须拆分**：
1. Windows 非计划重启后的 V4 SQLite 一致性、租约重新接管、safe-pause/no-send 恢复。
2. 真实无主机中断的连续 24 小时运行。
这两个目标不能互相替代。下一 GPT 应保持系统日志为只读，只汇报 event ID/time/安全数字字段，不披露包含个人信息的事件消息全文，不执行主机重启。

## 十三、最新隔离新增的 HMAC Replay Ledger 与预检测试注意事项

完整手册新增 next_gpt_status_probe.py（只读）和 isolated_host_replay_ledger.py（仅在 experiments/r2-* 内写独立数据库）及其测试。后者状态为 RESERVED_FOR_REVIEW、ALREADY_RESERVED 或 BLOCKED，**绝不授权浏览器发送**。首次 Windows CI 曾暴露测试 SQLite 连接未显式关闭导致的 WinError 32，已经修正；不得把失败的 workflow 当成 PASS。继任者以 PR **最终 SHA** 对应的最新 CI + 本机隔离测试为准，具体测试数应从 CI 日志读取而不是沿用 1,151 的旧版本统计。


## 十四、2026-10-09 08:35–08:38 最后实机验收与错误审计纠正

1. 实验源代码与交接说明在 SHA dc5a3b293bfa50a0beb5d7e06c55ef36321b3565 达成 GitHub Actions #37865467874 精确 SHA 的 SUCCESS：
   - V4 核心 516 项通过；
   - GUI Bridge 625 项通过；
   - Broker 29 项通过；
   - 总计 1,170 项通过，CANDIDATE_VALIDATION=PASS。
2. Windows 本机 Issue #2319 使用同一代码 SHA，在独立 r2-host-terminal-security-20261009 检出执行 516/516 项 V4 核心测试，0 failures/0 errors/0 skipped，且直接调用 next_gpt_status_probe.py 检查其 no-send 输出。观察器已到 seq=6；生产原 R1 仍 1 条模糊意图，GUI 仍 ERROR；旧活跃观察器工作树未改动。
3. 一次额外 TaskScheduler 查询 Issue #2320 曾返回 OLD_OBSERVER_TASK_IDENTITY_UNVERIFIED（FAILED）。**已确认为该额外审核脚本没有正确解析安装器 Inspect 的 JSON 输出**，不能据此判断任务遭到改写。
4. Issue #2321 改用原安装器自带 Inspect 并正确解析 JSON，证实旧活跃观察器代码精确 SHA 8da5085f906b4aeeece6fb0ae1f488bc597507b1、Git clean、Inspect identity_verified=true；任务 Ready，08:37:46 最近自动运行 ExitCode=0，下次调度 08:42:45，权限仍 Interactive/Limited。没有重装/重启/启停任务。

这个小插曲再次强调：新的 GPT 必须把 **审计脚本自身的错误** 与 **生产状态真实异常** 分开；不要看到一个单独探针 FAILED 就立即修改生产。旧任务的身份应以原安装器的可重复 Inspect、自身准确 SHA 和真实最近运行回执共同核查。

本文件自身更新为文档提交后，PR HEAD 将再次改变；**准确 HEAD 的最终 CI 必须重新核验**，不能把旧 dc5a SHA 的通过冒充新文档提交 SHA 的成功。安全代码不应再为纯状态记录盲目修改。
