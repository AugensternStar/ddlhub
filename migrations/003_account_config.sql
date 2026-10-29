-- 003_account_config.sql：自定义平台的登录配置（登录页/Cookie域/门户URL）。
ALTER TABLE account ADD COLUMN config_json TEXT;
