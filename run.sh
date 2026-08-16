#!/usr/bin/env bash
# 一鍵啟動。開工前準備 §2 第 7 項：P2／P3 能自己跑起後端。
set -e
cd "$(dirname "$0")"

if [ -d .venv/Scripts ]; then
  PY=.venv/Scripts/python.exe   # Windows
else
  PY=.venv/bin/python
fi

[ -f .env ] || cp .env.example .env

exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
