-- open-chat SQLite schema (development-plan.md §4)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT NOT NULL PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    collected_at TEXT NOT NULL,
    message_at TEXT NOT NULL,
    nick TEXT NOT NULL,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    tag_hints TEXT,
    UNIQUE (room_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_messages_room_message_at
    ON messages (room_id, message_at);

CREATE INDEX IF NOT EXISTS idx_messages_body
    ON messages (body);

CREATE TABLE IF NOT EXISTS collect_runs (
    run_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    room_id TEXT REFERENCES rooms(room_id),
    status TEXT NOT NULL,
    new_message_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_collect_runs_started_at
    ON collect_runs (started_at);

-- Reserved for phase 3+ (created now to avoid migration churn)
CREATE TABLE IF NOT EXISTS periodic_insights (
    insight_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    period_key TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_type TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    coverage TEXT,
    topics_json TEXT NOT NULL DEFAULT '[]',
    patch_reactions_json TEXT NOT NULL DEFAULT '[]',
    analyzer_model TEXT,
    analyzer_version TEXT NOT NULL,
    prompt_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (room_id, period_key, analyzer_version)
);

CREATE TABLE IF NOT EXISTS topic_stats (
    stat_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL,
    period_type TEXT NOT NULL,
    room_id TEXT,
    tag TEXT,
    topic_key TEXT,
    title TEXT,
    mentions INTEGER NOT NULL DEFAULT 0,
    distinct_nicks INTEGER NOT NULL DEFAULT 0,
    messages_referenced INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS patch_reaction_stats (
    stat_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL,
    patch_item TEXT NOT NULL,
    stance TEXT NOT NULL,
    mentions INTEGER NOT NULL DEFAULT 0,
    distinct_nicks INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_topic_stats_period_room
    ON topic_stats (period_key, room_id);

CREATE INDEX IF NOT EXISTS idx_topic_stats_tag_key
    ON topic_stats (tag, topic_key);

CREATE INDEX IF NOT EXISTS idx_patch_reaction_stats_period
    ON patch_reaction_stats (period_key);

CREATE INDEX IF NOT EXISTS idx_patch_reaction_stats_item
    ON patch_reaction_stats (patch_item);
