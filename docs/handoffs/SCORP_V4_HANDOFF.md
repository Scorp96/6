# SCORP V4 Master A + Dynamic Workers — 最终交接

日期：2026-09-14（Asia/Shanghai）
仓库：`Scorp96/666`
分支：`feature/v4-master-a-dynamic-workers`
工作树：`C:\ScorpAgent\worktrees\v4-transaction-core`
代码候选：`4de4ab272f5603db8705be05e49c9f151f9700bd`
实验安装：`C:\ScorpAgent\v4-core-lab`
PDF：`CANCELLED_BY_USER`（取消发生在任何 PDF 生成之前）

> Git 提交不能在自身内容中可靠写入自身哈希。证据提交因此由最终交付消息
> 单独记录；在本分支中可用 `git rev-parse HEAD` 读取。代码候选哈希是固定且
> 已嵌入所有清单/回执的身份。

## 最终目标

在不改动现有生产链的前提下，构建一个以 SQLite 为唯一可变权威的 SCORP V4
事务核心：单一逻辑 Master A、默认两个动态 Worker、持久任务 DAG/租约、浏览器
意图与 outbox、崩溃后保守恢复、确定性验收、旧 JSON 只读迁移、实验安装身份链。

## 当前阶段与结论

当前为“实验候选已实现并验证”，不是生产部署。独立验收器在实验 SQLite 上对
AC01–AC12 返回 `PASS`，阻断项为空；其中 AC12 是用户后来批准的 10 秒短时性能/
恢复测试，其 PASS 不证明 24 小时长期稳定性。真实浏览器门完成了一次独立新会话
提交与精确 ACK；崩溃注入仍在注入引擎上完成。

生产切换状态是 `NOT_AUTHORIZED_NOT_ATTEMPTED`，不是因为缺少旧 24 小时门而
BLOCKED。没有 merge、push、deploy、计划任务修改、服务修改或生产权威切换。

## 架构

1. `StateStore` 对每个连接强制 `journal_mode=DELETE`、`synchronous=FULL`、
   `foreign_keys=ON`、`busy_timeout=5000`，写事务使用 `BEGIN IMMEDIATE`。
2. Master A 只能提交受限 proposal，通过 `state_version + master_epoch +
   transition_id` 做 CAS、幂等和 fencing；不能直接写 SQL 或自我宣布完成。
3. Scheduler 固定两个并发槽，Worker 绑定 `worker_id / assignment_id / slot_id /
   lease_token`；依赖未满足、写资源冲突、租约过期、旧 epoch 或路径越界均拒绝。
4. BrowserAdapter 在任何可能提交前先持久化 `MAY_HAVE_SUBMITTED`；未知结果只读
   reconcile，只有正向证明“未提交”才允许重试，避免超时后盲目重复。
5. AcceptanceValidator 只读检查合同、候选、工件、任务、Worker、候选结果、
   outbox、浏览器歧义和审查 findings；它没有“写 PASS”接口。
6. V3 JSON 只能作为带原始 SHA-256 的 `IMPORTED_SNAPSHOT`；V4 导出标记为
   `READ_ONLY_EXPORT`，不建立双权威。

## 已完成的主要变更

- 新增 `scorp-agent/master_a_dynamic_v4/` 包与规范化 SQLite schema。
- 实现精确五态 commit 结果、并发 CAS、transition 内容冲突和 master fencing。
- 实现两槽动态调度、依赖释放、资源冲突串行化、路径/reparse 防逃逸和租约恢复。
- 实现浏览器意图、outbox、绑定/换绑、提交前持久化、响应捕获与崩溃恢复。
- 实现证据回执、发布候选、只读确定性验收、截止时间和迟到反证保留。
- 实现旧快照不可变导入与只读导出，不伪造 V4 历史。
- 实现严格 CSV + Decimal 的真实 T1/T2/T3 工作负载与稳定 JSON CLI。
- 实现候选逐文件清单、保护路径前后哈希、实验安装回执与安装后身份复核。
- 将原 24 小时 AC12 按用户最新要求替换为真实短时性能/恢复测试。
- 用户取消 PDF 后删除生成器和相关测试，保留 YAML + 完整 Markdown 交付。

### 本次桥接衔接增量（4de4ab2）

- 增加 `v4_bridge_gateway.py`：显式把根合同、动态 Worker、SQLite 浏览器意图和恢复串成一条候选入口。
- 增加 `gui_engine.py`：适配现有异步 ChatGPT GUI transport；认证探针缺失或非 `AUTHENTICATED` 时在浏览器 I/O 前阻塞。
- 旧桥接 JSON 写入改为唯一临时文件并带 Windows 替换重试；GitHub 评论读取分页；守护循环支持错误回调和连续失败停止。
- 旧 `Scorp96/scorp-control-plane` 只保留显式迁移兼容，V4 默认队列为 `Scorp96/666`。
- 这些新增入口只在候选 worktree 验证，未安装到生产桥接目录，也未修改计划任务。

## 已验证事实

### 冻结候选回归

- V4：42 tests，`4.077s`，0 failure/error。
- 完整 bridge 基线：397 tests，`11.654s`，0 failure/error。
- PowerShell：`V4_CRITICAL_HARDENING_PASS`、
  `V4_PRODUCTION_HARDENING_PASS`、`V4_SELFHEAL_HARDENING_PASS`。
- Python：35 个 V4 `.py` 文件以内存 `compile()` 编译通过。
- `git diff --check`：通过。

### 当前衔接候选验证

- V4 全量：46 tests，`3.790s`，0 failure/error。
- 完整 bridge：403 tests，`9.817s`，0 failure/error。
- 衔接定向测试：12 tests，0 failure/error；覆盖队列选择、SQLite gateway、异步 GUI seam、并发 JSON 写入、GitHub 分页和守护错误可见性。
- `compileall`：`master_a_dynamic_v4` 与 `chatgpt-gui-bridge` 编译通过。
- `git diff --check`：通过。
- 以上是当前提交的本地/模拟证据；没有重新执行真实浏览器、GitHub 写入或实验安装。

### 实验安装身份

- 候选清单内部哈希：
  `1c51c64c1c573fdeb5a026b22f1b40dfe1594c254b62b4f0816b6acc9971dc35`。
- 清单包含 21 个运行时文件；安装只复制这些文件。
- 安装回执的候选、源工作树、解释器、数据库和清单一致。
- 安装后 SQLite：`delete / 2 / 1 / 5000`，与合同一致。
- 受保护路径 before/after/fresh 清单 SHA-256 均为：
  `ac6d6b8ce0e3a8babb56e8afe9291352b10947aa7a06fd0f72f3ad2e4c7f7b5b`。
- 受保护范围：24 + 5,052 + 10 + 2,023 个文件；未观察到生产修改。

### AC12 短时实测

- 请求/实测时长：`10.0 / 10.201350499992259` 秒。
- 48 个 T1/T2/T3 循环，144 个任务验证完成。
- 吞吐：`14.115778102135524 tasks/s`。
- 峰值动态 Worker：2；槽位复用：142 次。
- 过期租约调度恢复：2 次；最大恢复：`0.03244540002197027s`，上限 2s。
- 浏览器意图/提交尝试：48/48；重复提交：0；错误：0。
- 数据库 SHA-256：
  `27ea8a4b99047a2e8fbf9bea7e3bb8ca78c30b6fe258c6b0a714923fdfabab5d`。
- 环境：Windows 11 build 26200、Python 3.15.0rc2、SQLite 3.53.2、8 logical CPUs。
- 浏览器范围是“真实本地 Adapter 状态机 + 注入引擎”，长期稳定性=`false`。

### 真实浏览器门

- Chrome `scorpion` profile，ChatGPT Plus 登录态可见，无 CAPTCHA/验证挑战。
- 唯一 marker：`SCORP_V4_REAL_BROWSER_GATE::682005bb1edab614::20260914`。
- 会话：`https://chatgpt.com/c/6aa72e9c-289c-83e8-8672-8bd40ff1b65d`。
- DOM 观测：marker=1、精确 ACK=1、提交动作=1、重复=0、生成结束。

### 独立验收器

- 状态：`PASS`；blockers：`[]`。
- 合同 SHA-256：
  `b65a3a9636b2dcec0e579459450d3ca0d86b21f4558056f91aeb51c0209bf7fb`。
- 验收 SQLite：`C:\ScorpAgent\v4-core-lab\state.sqlite3`。

## AC01–AC12 状态

| AC | 状态 | 证据范围 |
|---|---|---|
| AC01 | PASS | 五次两线程同版本竞争，均恰好一个 COMMITTED |
| AC02 | PASS | 幂等、内容冲突、epoch/version/lease fencing |
| AC03 | PASS | 真实 SQLite/outbox + 注入浏览器崩溃点 |
| AC04 | PASS | 缺证、伪证、漂移、活跃 Worker、未决意图均拒绝 |
| AC05 | PASS | 两槽、队列、依赖、短测动态循环 |
| AC06 | PASS | allowlist、前缀欺骗、relative、reparse、生产零变化 |
| AC07 | PASS | 单元恢复 + 一次真实浏览器提交/ACK |
| AC08 | PASS | 真实 Plus 登录门 + 登录/CAPTCHA/未知态阻断模拟 |
| AC09 | PASS | deadline、历史 verdict、迟到阻断反证 append-only |
| AC10 | PASS | 命名实验目录安装与候选/文件/数据库身份 |
| AC11 | PASS | 42 + 397 + 3 PowerShell + compile/diff |
| AC12 | PASS | 10.201 秒短时性能/恢复范围；不证明 24 小时长稳 |

## 软弃用与保留

`project_state_v3.py`、`session_registry_v3.py`、Worker pool、V3 transport/
relay、Windows-MCP driver、`executor-v4(.1).ps1`、`runner-v4(.1).ps1` 及四个
生产根全部保留。它们仍有 3–15 处代码/测试/安装/文档引用，也是当前回滚材料。
删除前仍要求引用清零、迁移验证、完整回归、归档回滚证据和单独的生产切换授权。

## 临时妥协、未验证事项与风险

- AC03 的 live-service 崩溃注入未做；真实服务只执行了正常的一次提交/响应。
- AC12 是短测，不能外推为 24 小时、周级或长期稳定性。
- 实验安装不是 Windows 服务/计划任务，也没有自动开机运行。
- 尚未执行真实生产流量、生产切换、回滚演练或负载极限测试。
- 当前 SQLite `DELETE + FULL` 偏可靠性，不代表已完成最高吞吐优化。
- 生产根的零变化是安装时与一次 fresh 复核的完整哈希证据；未来外部变化需重测。

## 推荐方案与执行顺序

1. 保持 `682005bb…` 作为不可改写代码候选，保留证据提交为其后代。
2. 先在实验目录进行更长但仍非生产的重复短测/重启测试，观察数据库增长与 P95。
3. 若要接生产浏览器驱动，先做真实提交后的进程中断恢复演练，并使用一次性测试会话。
4. 再设计有限 canary：明确停机/回滚点、生产路径清单、单写者切换与监控。
5. 只有单独授权后才允许 merge/push/deploy/计划任务或生产权威切换。

成功标准：候选/安装/证据身份一致，所有必需 AC 在其声明范围内 PASS，真实 canary
无重复/丢失/歧义，生产清单按授权变化且可回滚。停止条件：任何身份漂移、未知提交、
登录挑战、CAS/fencing 异常、路径越界、证据伪造、非零错误或未授权生产变化。

## 交付文件与 SHA-256

| 文件 | SHA-256 |
|---|---|
| `SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml` | `4ac55b2e026e50bcae3920524aa79e9a582363806b35464658bf08cd48c87fa1` |
| `SCORP_V4_CANDIDATE_MANIFEST.json` | `4f8f449bd467f57acb8e02b681e8b0b60e48233d7fb837f9330459f3bd57543c` |
| `SCORP_V4_LAB_INSTALL_RECEIPT.json` | `f35ec0fa922fa1c3b0fb70168f81d84d5b21c8c80cf876d57bde93d74e1c02b3` |
| `SCORP_V4_PROTECTED_PATH_MANIFEST.json` | `252f435af21b5116c2a636ee837fffb2e5e7700856b79737060583c29171086d` |
| `SCORP_V4_REAL_BROWSER_EVIDENCE.json` | `57d82868d93eafea3fed59526f1060d2032f9bfb8837bb6242b608db76837cc5` |
| `SCORP_V4_AC12_SHORT_SOAK.json` | `92e818f084b5039865ec146f63bdef36567b8673bd837df585c86c461e1cacb8` |
| `SCORP_V4_VERIFICATION.json` | `823eee1f9a2c476ad086c949dfef10dc2f6fcdaff92aec67299b6b7fd4b25e84` |
| `SCORP_V4_ACCEPTANCE_STATUS.json` | `eeaa611a3c280781ef53c2495a70f7e3960f13c864bb5180013298475763b34e` |

绝对目录：`C:\ScorpAgent\worktrees\v4-transaction-core\docs\handoffs`。

## 新聊天开场白

```text
继续 SCORP V4 Master A + Dynamic Workers。请先只读核验：仓库 Scorp96/666，分支 feature/v4-master-a-dynamic-workers，工作树 C:\ScorpAgent\worktrees\v4-transaction-core，当前代码候选 4de4ab272f5603db8705be05e49c9f151f9700bd。先阅读 docs/handoffs/SCORP_V4_HANDOFF.md、docs/handoffs/SCORP_V4_BRIDGE_INTEGRATION.md、SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml 与历史验收回执。当前候选新增桥接衔接已完成本地模拟验证；旧 682005bb 清单/安装回执属于历史冻结证据，不能直接当作 4de4ab2 的安装证据。没有 merge、push、deploy 或生产切换。任何下一步先重新核验当前 HEAD、候选/安装/工件 hash 和受保护生产路径，再提出 canary 或真实崩溃恢复方案；未经我明确授权不得改生产。
```
