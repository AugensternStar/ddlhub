-- 001_init.sql：Phase 2 初始库表。与 ddlhub/core/models.py 严格对应。
-- 约定：所有 DATETIME 一律存 naive UTC（ Asia/Shanghai 等本地时区仅在展示层转换）。

CREATE TABLE account (
    id            INTEGER PRIMARY KEY,
    uuid          VARCHAR(36)  NOT NULL UNIQUE,
    adapter_key   VARCHAR(64)  NOT NULL,
    label         VARCHAR(128) NOT NULL,
    username_hint VARCHAR(64)  NOT NULL DEFAULT '',
    status        VARCHAR(16)  NOT NULL DEFAULT 'ok',      -- ok | need_relogin | disabled
    last_ok_at    DATETIME,
    last_error    VARCHAR(2000) NOT NULL DEFAULT '',
    created_at    DATETIME     NOT NULL
);

CREATE TABLE session_cache (
    account_id INTEGER      NOT NULL PRIMARY KEY,
    enc_blob   BLOB         NOT NULL,
    key_ref    VARCHAR(128) NOT NULL,
    updated_at DATETIME     NOT NULL,
    expires_at DATETIME,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);

CREATE TABLE course (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER      NOT NULL,
    source_id  VARCHAR(255) NOT NULL,
    name       VARCHAR(255) NOT NULL,
    term       VARCHAR(64),
    url        VARCHAR(512),
    CONSTRAINT uq_course_account_source UNIQUE (account_id, source_id),
    CONSTRAINT uq_course_id_account     UNIQUE (id, account_id),
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);
CREATE INDEX ix_course_account ON course(account_id);

CREATE TABLE assignment (
    id                    INTEGER PRIMARY KEY,
    course_id             INTEGER      NOT NULL,
    account_id            INTEGER      NOT NULL,
    source_id             VARCHAR(512) NOT NULL,            -- core 组合键: course_source_id::local_id
    title                 VARCHAR(512) NOT NULL,
    description           TEXT,
    due_at                DATETIME,                          -- naive UTC；NULL=无截止
    due_at_prev           DATETIME,                          -- 最近一次变更前的值
    published_at          DATETIME,
    status                VARCHAR(16)  NOT NULL DEFAULT 'open', -- open|submitted|closed
    source_url            VARCHAR(512),
    estimated_minutes     INTEGER,
    priority              INTEGER      NOT NULL DEFAULT 0,   -- 0 普通 / 1 重要
    reminder_offsets_json VARCHAR(255),                      -- 单作业覆盖档位（JSON 数组，minutes_before）
    local_done            BOOLEAN      NOT NULL DEFAULT 0,   -- 本地态，同步永不覆盖
    local_ignored         BOOLEAN      NOT NULL DEFAULT 0,
    pinned                BOOLEAN      NOT NULL DEFAULT 0,
    change_hash           VARCHAR(64)  NOT NULL DEFAULT '',
    first_seen_at         DATETIME     NOT NULL,             -- overdue 护栏用
    updated_at            DATETIME     NOT NULL,
    CONSTRAINT uq_assignment_account_source UNIQUE (account_id, source_id),
    CONSTRAINT fk_assignment_course FOREIGN KEY (course_id, account_id)
        REFERENCES course(id, account_id) ON DELETE CASCADE
);
CREATE INDEX ix_assignment_due_at      ON assignment(due_at);
CREATE INDEX ix_assignment_done_status ON assignment(local_done, status);
CREATE INDEX ix_assignment_course      ON assignment(course_id);

CREATE TABLE sync_run (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER     NOT NULL,
    trigger       VARCHAR(16) NOT NULL,      -- manual | cron | startup
    status        VARCHAR(16) NOT NULL,      -- success | partial | failed | need_relogin | skipped
    started_at    DATETIME    NOT NULL,
    finished_at   DATETIME,
    ok_count      INTEGER     NOT NULL DEFAULT 0,
    fail_count    INTEGER     NOT NULL DEFAULT 0,
    error_summary VARCHAR(2000) NOT NULL DEFAULT '',
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);
CREATE INDEX ix_sync_run_account ON sync_run(account_id);

CREATE TABLE notification_log (
    id            INTEGER PRIMARY KEY,
    assignment_id INTEGER,                     -- 可空=系统级通知；作业删除时置 NULL
    channel       VARCHAR(32) NOT NULL,
    kind          VARCHAR(16) NOT NULL,        -- reminder | change | overdue | test
    minutes_before INTEGER,                    -- reminder 专用（正数）
    dedupe_key    VARCHAR(600) NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|sent|failed|skipped
    attempts      INTEGER     NOT NULL DEFAULT 0,
    summary       VARCHAR(1000) NOT NULL DEFAULT '',      -- 原始文案，供失败重发
    error         VARCHAR(1000) NOT NULL DEFAULT '',
    sent_at       DATETIME,
    created_at    DATETIME    NOT NULL,
    CONSTRAINT uq_notif_dedupe_channel UNIQUE (dedupe_key, channel)
);
CREATE INDEX ix_notif_status ON notification_log(status);

CREATE TABLE app_setting (
    key        VARCHAR(64) PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}'
);
