-- 002_course_enabled.sql：课程选择开关（设置页可按账号勾选要跟踪的课程）。
ALTER TABLE course ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1;
