# SCORP V4 候选版本交接：DEA5433

候选提交：`dea5433d9fa871dc440ec4d3dadb0dc7f58a5a62`

当前状态：`TEST_VERIFIED_LIVE_UNVERIFIED`。离线事务核心、任务图、Worker 租约、执行范围和证据门禁已通过；当前真实 Chrome Use 双 Worker canary 为 `BLOCKED_AMBIGUOUS`，没有写成通过。

## 普通网页 GPT 要做的事

1. 读取仓库根目录 `GPT_START_HERE.md`、`docs/handoffs/SCORP_V4_RELEASE_RECORD_DEA5433.json` 和 `docs/handoffs/SCORP_V4_WEB_GPT_PACKET.json`。
2. 先声明 `CAPABILITY=web_only`；Git 内容不会自动授予 Windows、Python、SQLite 或 Chrome 权限。
3. 根据用户根目标生成 root contract、验收标准和最多两个动态 Worker 的依赖任务图。
4. 只生成结构化计划和本地操作员交接，不声称已打开 Windows、发送 ChatGPT prompt、创建新 GPT 会话或完成项目。
5. 遇到登录、验证码、限流、提交歧义或缺少本地证据时输出 `BLOCKED`，停止重试。

可直接贴给普通网页 GPT：

```text
你是 SCORP V4 的规划与证据审查 GPT。先读取 GPT_START_HERE.md、docs/handoffs/SCORP_V4_RELEASE_RECORD_DEA5433.json 和 docs/handoffs/SCORP_V4_WEB_GPT_PACKET.json。当前候选提交是 dea5433d9fa871dc440ec4d3dadb0dc7f58a5a62。

先输出 CAPABILITY=web_only。你没有 Windows、Python、SQLite、Chrome 或浏览器会话权限，除非用户提供当前本地连接器 preflight 证据。请根据我的根目标生成 root_contract、acceptance_contract 和最多两个动态 Worker 的依赖任务图；不要声称运行、发送、创建聊天或完成项目。每项验收必须绑定 candidate_commit、实际命令或动作、观察状态、原始输出引用和 artifact_sha256。遇到登录、验证码、限流、浏览器提交歧义或缺少本地证据时输出 BLOCKED，并停止重试。
```

## Windows 本地操作员要做的事

1. 使用隔离目录 `C:\ScorpAgent\worktrees\v4-fast-runtime-command-core`，不要切生产目录。
2. 运行只读 preflight，核对候选提交、Python、SQLite、允许根目录和 Chrome 驱动身份。
3. 先只读核对并清理旧的 Chrome Use session/relay 状态；不要重发当前遗留的歧义 intent。
4. 只有 blocker 清除后，才用**新候选提交和新数据库**运行一次受控 canary；不要复用当前 `MAY_HAVE_SUBMITTED` 或 `BLOCKED_AMBIGUOUS` 的外部动作。
5. 只有看到 conversation URL、响应身份、SQLite intent 和 candidate result 全部绑定同一提交，才可进入双 Worker 完整验收。

## 当前证据

- 离线：V4 核心 `215/215`；GUI Bridge `527/527`；compileall PASS。
- 实机只读：新 tab 选择和 URL settle、填入但不点击发送诊断 PASS；`submit_actions=0`。
- 当前 canary：`C:/ScorpAgent/v4-core-lab/canary-dea5433-20260916/evidence.json`，SHA-256 `ce06b51e252782148d878b59c7fe832bf2ee9c83c3c7561a96511043c594a289`；两个 Worker intent 均为 `BLOCKED_AMBIGUOUS/CONVERSATION_URL_MISSING`，没有 conversation URL、响应哈希或 candidate result，`retry_count=0`。
- 禁止把当前 BLOCKED 改写为 LIVE PASS，也禁止盲目重发。

## 交付位置

- 当前发布记录：`docs/handoffs/SCORP_V4_RELEASE_RECORD_DEA5433.json`
- 当前验证记录：`docs/handoffs/SCORP_V4_VALIDATION_DEA5433.json`
- 当前候选清单：`docs/handoffs/SCORP_V4_CANDIDATE_MANIFEST_DEA5433.json`
- 当前 Web GPT packet：`docs/handoffs/SCORP_V4_WEB_GPT_PACKET.json`
