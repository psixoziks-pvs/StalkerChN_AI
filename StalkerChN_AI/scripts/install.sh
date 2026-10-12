#!/usr/bin/env bash
# StalkerChN_AI — установка сервера ИИ (Linux/macOS)
set -e
cd "$(dirname "$0")/.."
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
echo "Готово. Запуск: ./scripts/run_server.sh"
