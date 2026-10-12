#!/usr/bin/env bash
cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
python scripts/server/main.py
