#!/usr/bin/env bash
# Linux/macOS 辅助脚本。注意：无安全 keyring 后端的系统会拒绝启动并提示改用 vault 模式。
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "[DDLHub] 创建虚拟环境..."
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -e ".[real-adapters]" -q
.venv/bin/python -m playwright install chromium || echo "[DDLHub] chromium install skipped"
exec .venv/bin/python -m ddlhub
