# SCORP V4 候选版本交接：D0AA533

候选提交：`d0aa533f714b66406ca9ce78a5f81e472912dd1e`

当前状态是 `TEST_VERIFIED_LIVE_UNVERIFIED`。本版本的本地 SQLite、任务图、Worker 租约、执行范围和证据门禁已经通过离线回归；真实浏览器发送尚未绑定到本候选版本，因此没有写成通过。

## 你的工作（普通网页 GPT）

1. 读取仓库根目录 `GPT_START_HERE.md` 和 `docs/handoffs/SCORP_V4_RELEASE_RECORD_D0AA533.json`。
2. 明确声明 `CAPABILITY: web_only`，除非用户另行提供已注册的本地连接器和当前 preflight 证据。
3. 根据用户根目标输出根合同、验收标准和最多两个 Worker 的依赖任务图。
4. 只生成结构化交接内容，不声称已经打开 Windows、Chrome、SQLite 或新建 GPT 会话。
5. 对每一项验收只接受当前候选提交绑定的证据；`BLOCKED`、`NOT_RUN` 不能改写成 `PASS`。

可以直接把下面这段贴给普通网页 GPT：

```text
你是 SCORP V4 的规划与证据审查 GPT。先读取 GPT_START_HERE.md 和 docs/handoffs/SCORP_V4_RELEASE_RECORD_D0AA533.json。
当前候选提交是 d0aa533f714b66406ca9ce78a5f81e472912dd1e。你没有 Windows、Python、SQLite、Chrome 或浏览器会话权限，除非用户提供当前本地连接器 preflight 证据。
请先输出 CAPABILITY=web_only；然后根据我的根目标生成 root_contract、acceptance_contract 和最多两个动态 Worker 的依赖任务图。不要自行声称运行、发送、创建聊天或完成项目。每条验收必须绑定 candidate_commit、实际命令或动作、观察状态、原始输出引用和 artifact_sha256。遇到登录、验证码、限流、浏览器提交歧义或缺少本地证据时输出 BLOCKED，并停止重试。
```

## 你的工作（Windows 本地操作员）

1. 使用隔离工作目录 `C:\ScorpAgent\worktrees\v4-fast-runtime-command-core`，不要切换生产目录。
2. 先运行只读 preflight，确认候选版本、Python 运行时、SQLite 路径、允许目录和 Chrome 驱动身份一致。
3. 当前会话限流解除后，再做一次带唯一标记的单 Worker canary；确认提交 URL、响应身份和 SQLite intent 状态后，才允许双 Worker 测试。
4. 任何 `MAY_HAVE_SUBMITTED`、`BLOCKED_AMBIGUOUS`、登录失效、验证码或 URL 不一致都必须停在阻塞状态，不能盲目重发。
5. 实机证据必须单独记录为 `LIVE_VERIFIED`；在真实浏览器、重启恢复和连续运行证据齐全前，不得标记 `ACCEPTED`。

## 已验证和未验证

- 已验证：V4 核心 215/215；桥接回归 523/523；Python 编译；新标签选择与 URL 稳定的真实 Chrome Use 无发送诊断；不可变候选安装；Git bundle 备份。
- 未验证：当前候选版本的真实 ChatGPT 发送、两个真实 GPT 会话、浏览器重绑定、重启恢复、无人值守连续运行和生产切换。

