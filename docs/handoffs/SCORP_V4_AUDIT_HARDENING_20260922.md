# SCORP V4 审计修复交接（2026-09-22）

## 结论先行

审计报告中的 B01、B02、B03、B04、B05、B06 均已在独立分支完成代码修复，并由回归测试验证。旧的 `COMPLETE` 证据根、原生产配置和旧工作目录没有修改。

这次交付是**测试验证的修复候选**，不是新的真实浏览器验收结论：本轮没有向 ChatGPT 发送新的 Worker 提示，因此不能把本候选标成 `LIVE_VERIFIED` 或 `ACCEPTED`。

## 版本与隔离

| 项目 | 值 |
|---|---|
| 审计输入 | `C:\Users\scorp\.codex\attachments\a2213952-698d-4906-ae42-dbfe20b55fa9\已粘贴的文本.txt` |
| 审计基线 | `1f748c631a30bc71a9f6d98697b1aeb7a0ba9532` |
| 基线来源 | `origin/fix/v4-slow-worker-recovery-20260922` |
| 修复分支 | `fix/v4-audit-hardening-20260922` |
| 修复工作目录 | `C:\ScorpAgent\worktrees\v4-audit-hardening-20260922` |
| 代码修复提交 | `f1d19ba6f94ac687f90bcf5edde2a5e165f8c198` |
| 生产切换 | 未执行 |

修复提交只包含 `scorp-agent` 下的运行时代码和回归测试。交接文档在该提交之后追加；因此文档提交会是另一个 Git 节点，代码候选仍以表中的 `f1d19ba...` 为准。

## 审计问题处理矩阵

| ID | 原结论 | 当前状态 | 实际处理 |
|---|---|---|---|
| B01 | Worker 执行请求只靠提示词约束 | **FIXED** | `MasterAController` 在任何 LocalExecution 前检查 `execution_request_template`；缺失、审计任务夹带请求、模板篡改均拒绝。Windows 路径分隔符只做确定性规范化后再比较。 |
| B02 | `COMPLETE` 可被 OperatorControl 改回运行或取消 | **FIXED** | `COMPLETE`、`HARD_BLOCKED`、`TERMINAL` 在所有 operator mutation 前统一返回 `PROJECT_TERMINAL`。 |
| B03 | `block_intent()` 可降级已捕获结果 | **FIXED** | 只允许合法的准备/提交/歧义源状态进入 `BLOCKED_AMBIGUOUS`；`RESPONSE_CAPTURED`、`FENCED_AMBIGUOUS` 等状态被拒绝。 |
| B04 | worktree 创建后崩溃没有可验证恢复点 | **FIXED（fail-closed）** | 写任务在 `WORKTREE_PREPARED` 阶段持久化 receipt；重启只在 receipt、assignment、repository、HEAD、clean 状态全部重新核对后继续。没有准备阶段证据或已经越过命令开始 fence 时仍阻塞，不根据目录存在盲目重放。 |
| B05 | Arbiter 产生 daemon 没有 handler 的 action | **FIXED** | daemon action map 增加 `BLOCKED`、`EMERGENCY_STOP`、`FENCE_STALE_RESULTS`；紧急停止是终止安全停机；旧 epoch 结果通过 SQLite 事务标记为 `STALE` 并可重复调用而不循环制造动作。 |
| B06 | candidate binding 在 plan 校验中可选 | **FIXED（candidate-bound runtime）** | `MasterAController(candidate_commit=...)` 时要求每个任务的 `task_context.candidate_commit` 与候选提交完全一致；CLI runtime 将经过 manifest 校验的 candidate 传入控制器。未绑定 candidate 的通用单元测试仍可用于审计/兼容场景。 |
| B07 | Git 交接落后于实际状态 | **UPDATED** | 本文记录新的修复候选与验证边界；旧交接文件和旧证据不覆盖、不改写。 |
| B08 | 关键 invariant 缺少回归测试 | **FIXED FOR B01-B06** | 新增执行模板、终态 mutation、intent 降级、worktree recovery、stale action、candidate binding 等测试。 |
| B09 | 分支未发布收敛、无 CI combined status | **UNVERIFIED / NOT FIXED** | 修复分支尚未合并主分支，也没有把本候选宣称为 GitHub release。 |

## 关键实现边界

### Worker → LocalExecution

任务带有 `task_context.execution_request_template` 时，Worker 的 `COMPLETE` 结果必须提供结构化 `execution_request`，并且与授权模板的规范化 JSON 完全相同。任务没有模板时，`COMPLETE` 结果不得带 `execution_request`。校验发生在创建本地执行 intent 之前，因此失败不会触发 worktree、命令或浏览器副作用。

模板中的 `working_directory`、`repository`、`worktree` 和 `resource_paths` 统一把 Windows 反斜杠规范成正斜杠后比较；这只消除路径表示差异，不放宽字段、路径范围、模块白名单或访问模式。

### 写任务恢复

执行顺序现在是：

```text
LOCAL_EXECUTION intent
  → MAY_HAVE_SUBMITTED
  → git worktree prepare
  → durable WORKTREE_PREPARED receipt
  → durable EXECUTION_MAY_HAVE_SUBMITTED fence
  → LocalExecutionAdapter.execute
  → capture receipt
```

只有 `WORKTREE_PREPARED` receipt 存在时才会调用只读 `verify_prepared()`；它会重新核对 assignment、repository binding、scope、HEAD 和 clean 状态。命令开始 fence 之后的崩溃继续走原有 reconciliation/block 逻辑，避免 exactly-once 外部副作用被错误重放。

### Candidate 绑定

使用 `tools/v4_master_controller_runtime.py` 的候选运行时必须为每一个 task context 写入：

```json
{"candidate_commit":"<与 --candidate-commit 相同的 40 位提交哈希>"}
```

旧的普通审计/单元测试可不传 `candidate_commit`；但一旦构造控制器时传入 candidate，漏字段或不同字段都会在 plan admission 阶段失败，不会进入 Worker 执行。

## 验证证据

在 `C:\ScorpAgent\worktrees\v4-audit-hardening-20260922\scorp-agent` 下执行：

```text
python -m unittest discover -s master_a_dynamic_v4/tests -p "test_*.py"
```

结果：`Ran 284 tests ... OK`。

桥接与运行时全套：

```text
$env:PYTHONPATH="C:\ScorpAgent\worktrees\v4-audit-hardening-20260922\scorp-agent\chatgpt-gui-bridge"
python -m unittest discover -s chatgpt-gui-bridge/tests -p "test_*.py"
```

结果：`Ran 579 tests ... OK`。

另外已通过 `git diff --check`，并执行了真实 Windows Python 进程下的 SQLite、Git worktree、daemon、scheduler、桥接测试。测试输出中出现的 `retire` CLI usage 是一个测试用例验证“缺少发送参数时必须拒绝”，不是测试失败。

## 三种验收状态

| 状态 | 本候选结论 | 依据 |
|---|---|---|
| `TEST_VERIFIED` | **YES** | 284 个事务核心测试、579 个桥接/运行时测试通过，包含本次新增回归。 |
| `LIVE_VERIFIED` | **NO** | 本轮没有向真实 ChatGPT 新建/发送 Worker 任务；没有新的 `/c/<id>`、响应 capture 或真实 LocalExecution E2E 证据。 |
| `ACCEPTED` | **NO** | completion gate、candidate manifest、真实 Windows + Chrome 两 Worker canary 尚未绑定到 `f1d19ba...`。 |

之前基线的 live acceptance 证据仍属于 `1f748c6` 及其历史证据，不自动继承到本修复候选。

## 下一步顺序

1. 在已登录的 Windows 交互会话中，对 `f1d19ba...` 重新生成 candidate manifest，并把每个任务的 `candidate_commit` 写入 plan。
2. 先跑只读 preflight，确认 ChatGPT 会话可读、没有验证码、daemon 和 SQLite 指向本候选。
3. 只做一次单 Worker browser canary，逐项记录 `PREPARED → MAY_HAVE_SUBMITTED → /c/<id> → RESPONSE_CAPTURED`；出现 ambiguous 时停止，禁止 blind retry。
4. 单 Worker 通过后，再做两 Worker 真实提交，验证两个独立 assignment/conversation 和槽位复用。
5. 重新跑 LocalExecution、worktree crash recovery、daemon restart，并把证据绑定到同一 candidate。
6. 只有所有必需 acceptance evidence 都是 `PASS`，才更新 release manifest、当前 handoff 和生产切换记录；不要把本地测试的 `OK` 改写成 `ACCEPTED`。

## 回滚与停止条件

- 旧 `COMPLETE` evidence root 不可覆盖；生产切换前使用原分支和原安装。
- 浏览器提交状态为 `MAY_HAVE_SUBMITTED`、`CONFIRMED_SUBMITTED` 或 `BLOCKED_AMBIGUOUS` 时，只读核对，不重发。
- candidate、manifest、安装目录或运行进程不一致时停止，不用“最新目录”替代验收版本。
- 发现 Windows 登录失效、验证码、无法判定的提交结果或本地权限越界时，保持 `BLOCKED` 并保存证据。
