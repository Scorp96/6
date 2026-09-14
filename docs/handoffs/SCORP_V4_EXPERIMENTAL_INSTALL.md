# SCORP V4 experimental installation

This installation is an isolated experiment, not a production cutover. The default
target is `C:\ScorpAgent\v4-core-lab`; the installer refuses overlap with the source
tree or any protected production root.

## Identity chain

The candidate manifest binds one full Git commit to an explicit allowlist of files,
their byte lengths, and SHA-256 values. Installation rechecks the live Git `HEAD`,
manifest hash, source-tree identity, and every source hash before copying. It copies
only listed files, creates a fresh SQLite database with the V4 durability settings,
and records source commit, source tree, Python interpreter, database path, manifest
hash, and installation time in `install-receipt.json`.

Protected roots are recursively hashed immediately before and after installation:

- `C:\ScorpAgent\state-v3\active`
- `C:\ScorpAgent\state-v4`
- `C:\ScorpAgent\gpt-native-v4`
- `C:\ScorpAgent\chatgpt-gui-bridge-runtime`

Any change aborts acceptance. The installer does not register a scheduled task,
change a Windows service, alter the existing browser bridge, merge a branch, push a
commit, or switch production authority.

## Invocation

From the exact candidate worktree, generate and independently retain the canonical
manifest, then run:

```powershell
& .\scorp-agent\master_a_dynamic_v4\install-lab.ps1 `
  -SourceRoot 'C:\ScorpAgent\worktrees\v4-transaction-core' `
  -CandidateCommit '<40-character candidate commit>' `
  -ManifestPath '<absolute candidate-manifest.json>'
```

The target must be absent or empty. Existing non-empty lab state is preserved and
causes a fail-closed stop. Removal or replacement requires a separately reviewed,
explicit operation.

## Acceptance boundary

AC10 passes only when candidate, manifest, installed files, receipt, interpreter,
database, and unchanged protected-path hashes all agree. This does not establish
AC12, production readiness, or production deployment.
