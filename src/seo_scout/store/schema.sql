CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    start_url     TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    pages         INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    settings_json TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url                 TEXT    NOT NULL,
    final_url           TEXT    NOT NULL,
    status              INTEGER NOT NULL,
    depth               INTEGER NOT NULL,
    content_type        TEXT,
    bytes               INTEGER NOT NULL,
    elapsed_ms          INTEGER NOT NULL,
    fetched_at          TEXT    NOT NULL,
    html                TEXT,
    redirect_chain_json TEXT    NOT NULL,
    headers_json        TEXT    NOT NULL,
    error               TEXT,
    UNIQUE (run_id, url)
);

CREATE TABLE IF NOT EXISTS links (
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    from_url TEXT    NOT NULL,
    to_url   TEXT    NOT NULL,
    UNIQUE (run_id, from_url, to_url)
);

CREATE TABLE IF NOT EXISTS sitemap_urls (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url    TEXT    NOT NULL,
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_pages_run ON pages (run_id);
CREATE INDEX IF NOT EXISTS idx_links_run_to ON links (run_id, to_url);

CREATE TABLE IF NOT EXISTS issues (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url      TEXT    NOT NULL,
    rule_id  TEXT    NOT NULL,
    severity TEXT    NOT NULL,
    message  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS page_scores (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url    TEXT    NOT NULL,
    score  INTEGER NOT NULL,
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_issues_run_url ON issues (run_id, url);
CREATE INDEX IF NOT EXISTS idx_issues_run_rule ON issues (run_id, rule_id);

CREATE TABLE IF NOT EXISTS ai_cache (
    key             TEXT PRIMARY KEY,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    status          TEXT NOT NULL,
    suggestion_json TEXT,
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url               TEXT    NOT NULL,
    kind              TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    usd               REAL    NOT NULL,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url              TEXT    NOT NULL,
    original_title   TEXT,
    original_meta    TEXT,
    diagnosis        TEXT,
    title            TEXT,
    meta_description TEXT,
    status           TEXT    NOT NULL,
    reason           TEXT,
    cached           INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL    NOT NULL DEFAULT 0,
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_ai_calls_run ON ai_calls (run_id);
