CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imported_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size INTEGER NOT NULL CHECK (source_size >= 0),
    source_mtime_ns INTEGER,
    schema_name TEXT,
    status TEXT NOT NULL,
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    project_id TEXT PRIMARY KEY,
    root_contract_json TEXT NOT NULL,
    root_contract_sha256 TEXT NOT NULL,
    acceptance_contract_json TEXT NOT NULL,
    acceptance_contract_sha256 TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    imported_snapshot_id TEXT REFERENCES imported_snapshots(snapshot_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_state (
    project_id TEXT PRIMARY KEY REFERENCES contracts(project_id) ON DELETE RESTRICT,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    master_epoch INTEGER NOT NULL DEFAULT 0 CHECK (master_epoch >= 0),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    phase TEXT NOT NULL DEFAULT 'BOOTSTRAP',
    completion_candidate_commit TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS master_sessions (
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    master_epoch INTEGER NOT NULL CHECK (master_epoch >= 0),
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    PRIMARY KEY (project_id, session_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS master_sessions_one_active
ON master_sessions(project_id)
WHERE state = 'ACTIVE';

CREATE TABLE IF NOT EXISTS task_nodes (
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    objective_sha256 TEXT NOT NULL,
    resource_scope_json TEXT NOT NULL,
    access_mode TEXT NOT NULL DEFAULT 'write',
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    state TEXT NOT NULL DEFAULT 'QUEUED',
    result_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id, depends_on_task_id),
    FOREIGN KEY (project_id, task_id) REFERENCES task_nodes(project_id, task_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, depends_on_task_id) REFERENCES task_nodes(project_id, task_id) ON DELETE RESTRICT,
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    master_epoch INTEGER NOT NULL CHECK (master_epoch >= 0),
    base_state_version INTEGER NOT NULL DEFAULT 0 CHECK (base_state_version >= 0),
    lease_token TEXT NOT NULL UNIQUE,
    objective_sha256 TEXT NOT NULL,
    resource_scope_json TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id, task_id) REFERENCES task_nodes(project_id, task_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS leases (
    lease_token TEXT PRIMARY KEY REFERENCES assignments(lease_token) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL,
    master_epoch INTEGER NOT NULL CHECK (master_epoch >= 0),
    state TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS leases_one_active_slot
ON leases(project_id, slot_id)
WHERE state = 'ACTIVE';

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0 CHECK (processed IN (0, 1)),
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS transitions (
    transition_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    expected_version INTEGER NOT NULL,
    master_epoch INTEGER NOT NULL,
    proposal_sha256 TEXT NOT NULL,
    evidence_refs_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    result TEXT NOT NULL,
    committed_version INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_intents (
    intent_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    actor_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    predecessor_intent_id TEXT REFERENCES action_intents(intent_id),
    conversation_url TEXT,
    remote_identity TEXT,
    response_json TEXT,
    response_sha256 TEXT,
    ambiguity_reason TEXT,
    observation_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL UNIQUE REFERENCES action_intents(intent_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    available_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS browser_bindings (
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    conversation_url TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    predecessor_url TEXT,
    rebind_reason TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, channel)
);

CREATE TABLE IF NOT EXISTS candidate_results (
    result_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
    lease_token TEXT NOT NULL,
    master_epoch INTEGER NOT NULL,
    result_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PENDING',
    verified_result_sha256 TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_receipts (
    evidence_ref TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    acceptance_id TEXT NOT NULL,
    result TEXT NOT NULL,
    candidate_commit TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    command_or_action TEXT NOT NULL,
    observed_state_json TEXT NOT NULL,
    raw_output_reference TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_deadlines (
    project_id TEXT NOT NULL REFERENCES contracts(project_id) ON DELETE CASCADE,
    review_id TEXT NOT NULL,
    deadline TEXT NOT NULL,
    verdict TEXT,
    closed_at TEXT,
    PRIMARY KEY (project_id, review_id)
);

CREATE TABLE IF NOT EXISTS review_findings (
    project_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    late INTEGER NOT NULL CHECK (late IN (0, 1)),
    resolved_at TEXT,
    PRIMARY KEY (project_id, review_id, finding_id),
    FOREIGN KEY (project_id, review_id) REFERENCES review_deadlines(project_id, review_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS release_candidates (
    project_id TEXT PRIMARY KEY REFERENCES contracts(project_id) ON DELETE CASCADE,
    candidate_commit TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    installed_commit TEXT,
    installed_manifest_sha256 TEXT,
    verified_at TEXT
);
