PRAGMA foreign_keys = ON;
CREATE TABLE memory_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE memory_items (
    memory_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('episodic','semantic','procedural')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL CHECK (scope IN ('global','project')),
    project_id TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('active','disabled','superseded','deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    deleted_at TEXT,
    CHECK ((scope = 'global' AND project_id IS NULL) OR (scope = 'project' AND project_id IS NOT NULL)),
    CHECK ((status = 'deleted' AND deleted_at IS NOT NULL) OR (status != 'deleted' AND deleted_at IS NULL))
);
CREATE INDEX memory_items_scope_idx ON memory_items(scope,project_id,status,updated_at);
CREATE INDEX memory_items_kind_idx ON memory_items(kind,status,updated_at);
CREATE TABLE memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    excerpt TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX memory_evidence_memory_idx ON memory_evidence(memory_id,created_at);
CREATE INDEX memory_evidence_session_idx ON memory_evidence(session_id,created_at);
CREATE UNIQUE INDEX memory_evidence_unique_idx
ON memory_evidence(memory_id,session_id,IFNULL(turn_id,''),content_sha256);
CREATE TABLE memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('episodic','semantic','procedural')),
    content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    turn_id TEXT,
    project_id TEXT,
    memory_id TEXT REFERENCES memory_items(memory_id) ON DELETE SET NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL CHECK (status IN ('pending','selected','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX memory_candidates_status_idx ON memory_candidates(status,created_at,candidate_id);
CREATE INDEX memory_candidates_session_idx ON memory_candidates(session_id,created_at,candidate_id);
CREATE UNIQUE INDEX memory_candidates_memory_idx ON memory_candidates(memory_id) WHERE memory_id IS NOT NULL;
CREATE TABLE memory_watermarks (
    source_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL CHECK (position >= 0),
    event_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE memory_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('extract','consolidate','rebuild_projections')),
    status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    source_id TEXT,
    project_id TEXT,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (attempts <= max_attempts),
    CHECK (
        (status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status != 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX memory_jobs_claim_idx ON memory_jobs(status,available_at,lease_expires_at,created_at);
CREATE UNIQUE INDEX memory_jobs_active_source_idx
ON memory_jobs(kind,IFNULL(source_id,''),IFNULL(project_id,''))
WHERE status IN ('pending','running');
CREATE VIRTUAL TABLE memory_items_fts USING fts5(
    memory_id UNINDEXED,
    title,
    summary,
    content,
    tags,
    tokenize = 'unicode61'
);
CREATE TRIGGER memory_items_fts_insert AFTER INSERT ON memory_items
WHEN new.status = 'active'
BEGIN
    INSERT INTO memory_items_fts(memory_id,title,summary,content,tags)
    VALUES (new.memory_id,new.title,new.summary,new.content,new.tags_json);
END;
CREATE TRIGGER memory_items_fts_delete AFTER DELETE ON memory_items
BEGIN
    DELETE FROM memory_items_fts WHERE memory_id = old.memory_id;
END;
CREATE TRIGGER memory_items_fts_update AFTER UPDATE ON memory_items
BEGIN
    DELETE FROM memory_items_fts WHERE memory_id = old.memory_id;
    INSERT INTO memory_items_fts(memory_id,title,summary,content,tags)
    SELECT new.memory_id,new.title,new.summary,new.content,new.tags_json
    WHERE new.status = 'active';
END;
PRAGMA user_version = 1;
