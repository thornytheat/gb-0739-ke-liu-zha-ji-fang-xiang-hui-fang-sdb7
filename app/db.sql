CREATE TABLE IF NOT EXISTS runs (
    sequence INTEGER PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_files (
    run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    content BLOB NOT NULL,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS release_batches (
    sequence INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    batch TEXT NOT NULL,
    version INTEGER NOT NULL,
    station TEXT NOT NULL,
    direction TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    released INTEGER NOT NULL,
    entered_by TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    verifier TEXT,
    verified_at TEXT,
    verify_note TEXT NOT NULL DEFAULT '',
    void_note TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, batch, version)
);
CREATE TABLE IF NOT EXISTS release_ledgers (
    sequence INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    ledger_id TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    published_at TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
