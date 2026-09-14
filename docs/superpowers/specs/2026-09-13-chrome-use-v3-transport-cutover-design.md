# Chrome-Use V3 Transport Cutover Design

## Goal

Make `chrome-use` the authoritative browser execution backend for Master/Worker V3 in the isolated P0 branch, while preserving Scorp's durable control-plane semantics and removing Windows/foreground GUI control from the P0 acceptance path.

## Verified problem

The current V3 runtime defaults to `WindowsMcpActorDriverV3`. The active legacy bridge path reaches `gui_transport.run_live_turn` / `run_role_turn`, which starts `windows-mcp==0.8.5` with App/Shortcut/Clipboard/Click/Type/Wait/Snapshot tools and launches Chrome windows. The legacy driver also contains HWND foreground/window operations. In parallel, the P0 `ChromeUseActorDriverV3` already provides canonical URL binding, read-only recovery, and conversation-to-session reuse.

This dual-runtime model creates unnecessary foreground/window coupling and makes browser behavior harder to reason about.

## Architecture

Keep these Scorp components unchanged and authoritative:

- `ContinuationWatchdog`
- `MasterStateTransitionV3`
- `MasterWorkerCoordinatorV3`
- `ParallelMasterWorkerRelayV3`
- `DurableActorTransportV3`
- `ActorGuiBackendV3`
- `SessionRegistryV3`
- `WorkerConversationPoolV3`
- root objective / acceptance hashes
- leases, CAS/state-version rules, exactly-once and evidence gates

Replace only the default V3 browser-driver construction:

```text
Scorp D/A/Worker control plane
        -> ParallelMasterWorkerRelayV3
        -> DurableActorTransportV3
        -> ActorGuiBackendV3
        -> ChromeUseActorDriverV3
        -> ChromeUseCliV3
        -> chrome-use native session/tab runtime
        -> logged-in ChatGPT Web
```

`WindowsMcpActorDriverV3` remains available only as an explicit legacy transport for rollback/diagnostics. It must not be the P0 default and must not be silently selected when chrome-use configuration is missing.

## Configuration

`ProductionV3Runtime` remains driver-injectable. `bridge_worker.py` gains an explicit V3 transport selection and chrome-use executable path.

Required behavior:

- default V3 transport: `chrome-use`
- `--v3-transport chrome-use|windows-mcp`
- `--chrome-use-executable <absolute path>` is required for `chrome-use`
- chrome-use driver state defaults under the V3 project root as `chrome-use-driver-v3.json`
- missing chrome-use executable/configuration fails closed; there is no automatic fallback to Windows-MCP
- explicit `windows-mcp` remains possible only when deliberately selected

## Session ownership

Do not build another tab/session manager.

The existing relay remains responsible for semantic ownership:

- Master conversation URL comes from `SessionRegistryV3`
- Worker conversation URL comes from `WorkerConversationPoolV3`
- `ChromeUseActorDriverV3` maps canonical conversation URL to its persistent chrome-use session
- a new temporary chrome-use turn session is allowed only during first-chat bootstrap before a canonical `/c/...` URL exists
- after canonical binding, subsequent turns reuse the existing conversation/session

No acceptance path may require foreground Chrome, HWND focus, coordinate clicks, Alt+Space, manual tab switching, or one-new-tab-per-turn behavior.

## g735 handling

`#735` is never resubmitted. Its persisted driver state already proves a canonical conversation/session was created. Any further inspection of that turn is read-only and recovery-only.

## Production boundary

This change is isolated to `p0/chatgpt-transport-bakeoff`. It does not modify `production-e2e-g274` or `C:\ScorpAgent\state-v3\active`. Production remains HARD_BLOCKED until authoritative project state is independently located and verified.

## Tests / acceptance

Unit and integration tests must prove:

1. chrome-use is the default V3 transport construction.
2. missing chrome-use executable fails closed rather than falling back.
3. explicit legacy `windows-mcp` selection still constructs the legacy driver.
4. an injected driver still overrides transport construction for deterministic tests.
5. existing relay/session-registry/worker-pool tests continue to pass.
6. existing `ChromeUseActorDriverV3` exactly-once/recovery tests continue to pass.
7. a V3 composition test verifies that multiple logical turns with an existing canonical conversation do not create a new chrome-use turn session.
8. P0 live acceptance later requires bounded tab/session growth and zero OS-level foreground/window interaction.

## Stop conditions

Stop and fail closed if:

- runtime attempts to auto-fallback from chrome-use to Windows-MCP;
- a live turn requires HWND/foreground focus or coordinate input;
- session/tab count grows linearly with logical turns after canonical binding;
- any ambiguous submit path issues a second submit;
- production state is reconstructed or inferred.