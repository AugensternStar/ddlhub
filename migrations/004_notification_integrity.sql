-- 004_notification_integrity.sql（审计 06/07）：
-- 1) 清理孤儿通知（作业已删除但通知残留——旧表无外键导致）
-- 2) 重建通知表：补 assignment 外键（ON DELETE SET NULL）+ claimed_at 原子领取列
-- 注意：迁移由 db.py 的显式事务执行（BEGIN IMMEDIATE），失败整体回滚。

DELETE FROM notification_log
 WHERE status IN ('pending', 'failed', 'sending')
   AND assignment_id IS NOT NULL
   AND assignment_id NOT IN (SELECT id FROM assignment);

UPDATE notification_log
   SET assignment_id = NULL
 WHERE assignment_id IS NOT NULL
   AND assignment_id NOT IN (SELECT id FROM assignment);

CREATE TABLE notification_log_v2 (
    id             INTEGER PRIMARY KEY,
    assignment_id  INTEGER,
    channel        VARCHAR(32) NOT NULL,
    kind           VARCHAR(16) NOT NULL,
    minutes_before INTEGER,
    dedupe_key     VARCHAR(600) NOT NULL,
    status         VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|sending|sent|failed|skipped
    attempts       INTEGER NOT NULL DEFAULT 0,
    summary        VARCHAR(1000) NOT NULL DEFAULT '',
    error          VARCHAR(1000) NOT NULL DEFAULT '',
    sent_at        DATETIME,
    claimed_at     DATETIME,
    created_at     DATETIME NOT NULL,
    CONSTRAINT uq_notif_dedupe_channel UNIQUE (dedupe_key, channel),
    CONSTRAINT fk_notif_assignment FOREIGN KEY (assignment_id)
        REFERENCES assignment(id) ON DELETE SET NULL
);

INSERT INTO notification_log_v2
    (id, assignment_id, channel, kind, minutes_before, dedupe_key,
     status, attempts, summary, error, sent_at, claimed_at, created_at)
SELECT
    id, assignment_id, channel, kind, minutes_before, dedupe_key,
    status, attempts, summary, error, sent_at, NULL, created_at
  FROM notification_log;

DROP TABLE notification_log;
ALTER TABLE notification_log_v2 RENAME TO notification_log;
CREATE INDEX ix_notif_status ON notification_log(status);
