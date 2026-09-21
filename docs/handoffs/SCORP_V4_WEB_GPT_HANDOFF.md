# SCORP V4：给普通网页版 GPT 的交接说明

Git 传递代码、计划和证据，SQLite 是本地权威状态，Windows 宿主才可以运行命令，ChromeUse 适配器才可以在显式门禁下提交浏览器动作。普通网页版 GPT 读到仓库并不会自动获得本机权限。

当前候选是 `d814cb0384616520e89932e032e70a22d00abe3f`（GPT-5.6 Sol 兼容路径）。它通过离线验证，并完成真实浏览器单 Worker 与双 Worker canary；这只证明浏览器提交、响应捕获和 SQLite 意图收尾，不等于真实代码任务、重启恢复、无人值守或生产验收。

## 当前证据

- 离线：V4 核心 `270/270 PASS`，GUI Bridge `576/576 PASS`，broker `29/29 PASS`，candidate validation `PASS`。
- 真实单 Worker：`C:\ScorpAgent\v4-live-d814cb0\single-evidence.json`，原始文件 SHA-256 `6f55c5cb403082d53c29c73ff68cc229bd0ce15ee5f310cb22d2a5651eef18e6`。
- 真实双 Worker：`C:\ScorpAgent\v4-live-d814cb0\double-evidence.json`，两个 Worker 都 `RESPONSE_CAPTURED`，原始文件 SHA-256 `331bcf59d9c1717b01154b1fd9df5e2376d3f6fe389b9e164a40f08a375b59f0`。
- 候选清单：`docs/handoffs/SCORP_V4_CANDIDATE_MANIFEST_D814CB0.json`，规范 SHA-256 `b53e358602698a1e89c6d6becd8fbd4cf65f4b2be3ba64ada7f5977d6a2d2502`。
- 机器记录：`docs/handoffs/SCORP_V4_VALIDATION_D814CB0.json`、`docs/handoffs/SCORP_V4_RELEASE_RECORD_D814CB0.json`。
- `TEST_VERIFIED=true`；`LIVE_BROWSER_CANARY_VERIFIED=true`；整体 `LIVE_VERIFIED=false`；`ACCEPTED=false`。

## 网页 GPT 开场指令

你是 SCORP V4 的规划与验收端，不是本地 Windows 执行进程。先读取 `GPT_START_HERE.md`、本交接 JSON/Markdown 和当前 validation/release record。当前候选是 `d814cb0384616520e89932e032e70a22d00abe3f`，模型路径是 GPT-5.6 Sol；不要把仓库名 6 解释为 GPT-6。没有本地预检和当前运行证据时只能输出计划或 BLOCKED。收到 Windows 回执后区分 PASS、FAIL、BLOCKED、NOT_RUN；遇到 MAY_HAVE_SUBMITTED、无 URL、无响应哈希或任何不明确副作用，fail closed，不得盲目重发。

## 下一阶段

绑定真实代码工作负载，再验证 `WORK_RESULT/1 -> LocalExecutionAdapter -> candidate_results`，然后做 rebind/restart 和其他剩余门禁。缺证据不能把整体标成 `LIVE_VERIFIED` 或 `ACCEPTED`。
