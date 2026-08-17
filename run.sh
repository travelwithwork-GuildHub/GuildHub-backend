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

# ws-ping-*：[R25] 的逾時偵測。瀏覽器對「離開」沒有可靠訊號，靜止的人又
# 不送位置封包，所以存活只能靠 ping/pong 探測。寫成明確參數而不是靠預設值，
# 是為了讓這個數字是有人決定過的。
exec "$PY" -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --ws-ping-interval 20 --ws-ping-timeout 20 "$@"
