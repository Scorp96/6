# SCORP V4 机器狗默认后台浏览器策略（2026-09-23）

## 结论

已确认机器狗频繁弹出/抢占 Chrome 的直接原因：Chrome Use 驱动在编辑器、Send 控件、结果快照和恢复读取前反复调用 `bringToFront`。这与 V3 设计要求的“无前台窗口依赖”冲突。

现在默认策略已改为后台运行：真实 `ChromeUseCliV3` 默认不调用 `bringToFront`，所以普通 daemon、Master A 和 Worker 读取不会反复把 Chrome 拉到前台。需要人工观察的 canary 才能显式开启前台模式。

## 变更

- `ChromeUseCliV3(interactive=False)` 为默认值；`prepare_interactive()` 在默认模式只返回状态，不发浏览器前台命令。
- `ChromeUseActorDriverV3` 的结果快照和恢复路径通过受控准备函数执行；真实 Chrome Use 默认不前台，旧注入测试/兼容客户端仍保留原调用契约。
- `bridge_worker.py` 增加 `--chrome-use-interactive`，默认关闭。
- `v4_master_controller_runtime.py` 增加 `--chrome-use-interactive`，默认关闭。
- `v4_live_two_worker_canary.py` 增加 `--chrome-use-interactive`，默认关闭。
- `GPT_START_HERE.md` 已记录后台默认和显式前台边界。

显式前台只用于人工监督的 canary：

```powershell
python .\scorp-agent\chatgpt-gui-bridge\tools\v4_live_two_worker_canary.py `
  --send-canary `
  --chrome-use-interactive `
  ...其余候选绑定参数...
```

没有 `--chrome-use-interactive` 时，不应期待机器狗主动把 Chrome 窗口显示到前台；如果后台 transport 无法解析 Send 控件，应保持阻塞并记录证据，不能为了“看起来成功”自动前台或盲目重试。

明确选择 `windows-mcp` 的旧 transport 仍然具有前台/窗口依赖；它不是 V3 默认 transport，也没有被伪装成后台无窗口运行。

## 验证

- 先写失败测试验证默认 `prepare_interactive()` 不执行 runner；失败原因为旧实现仍返回 `bringToFront`。
- 修复后相关回归：`93 tests ... OK`。
- 完整桥接测试：`582 tests ... OK`。
- `git diff --check` 通过。

本次只验证代码行为和离线/测试 transport。没有发送新的真实 ChatGPT prompt，因此不能把这次改动标记为 `LIVE_VERIFIED` 或 `ACCEPTED`。真实浏览器的单 Worker canary 仍应先于双 Worker canary 执行，并继续遵守 ambiguous side effect 的 fail-closed 规则。

