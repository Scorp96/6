# SCORP V4 当前候选操作交接（D814CB0）

- 仓库：`Scorp96/6`
- 分支：`fix/persistent-master-p0-fix-20260921`
- 候选提交：`d814cb0384616520e89932e032e70a22d00abe3f`
- 隔离工作树：`C:\ScorpAgent\worktrees\v4-persistent-master-p0-fix-20260921`
- 候选清单规范 SHA-256：`b53e358602698a1e89c6d6becd8fbd4cf65f4b2be3ba64ada7f5977d6a2d2502`

## 已验证

V4 核心 270/270、GUI Bridge 576/576、broker 29/29 和 candidate validation 均 PASS。真实 Windows + 已登录 Chrome + SQLite 单 Worker、双 Worker 都完成提交、响应核对和意图收尾；Git 中的证据是 `docs/handoffs/SCORP_V4_LIVE_CANARY_D814CB0_SINGLE.json`、`docs/handoffs/SCORP_V4_LIVE_CANARY_D814CB0_DOUBLE.json`，本机原始证据分别位于 `C:\ScorpAgent\v4-live-d814cb0`。

## 仍未验收

`LIVE_VERIFIED=false`、`ACCEPTED=false` 保持不变。真实业务代码任务、`WORK_RESULT/1 -> LocalExecutionAdapter -> candidate_results`、physical rebind、daemon 重启恢复、未登录边界、无人值守 soak、生产安装和切换没有当前候选的绑定证据。

## 网页 GPT 的角色

普通网页版 GPT 可读取交接、生成任务图、审查证据并指出下一步；它没有本地 Windows、SQLite、Git 或 Chrome 权限。Windows 宿主必须运行候选代码并把命令、退出码、状态、原始输出和哈希写回。遇到 `MAY_HAVE_SUBMITTED`、无 URL、无响应哈希或不明确的浏览器副作用，保持 `BLOCKED`，不得盲目重发。
