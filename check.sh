#!/usr/bin/env bash
# Проверка одной командой: типы (basedpyright), затем тесты (pytest). Сеть и ключ OpenAI не нужны.
#   bash check.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
if [ ! -x "$PY" ]; then
  uv venv --quiet .venv
  if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
fi
uv pip install --quiet --python "$PY" -r requirements.txt -r requirements-dev.txt

uvx --quiet basedpyright --level error --pythonpath "$PY" backend tests eval
PYTHONIOENCODING=utf-8 "$PY" -m pytest -q -W error::DeprecationWarning
