#!/usr/bin/env bash
# [D02] 部署腳本。在 VM 上執行。
#
# 沒有 Docker、沒有 CI（開工前準備 §1：三人專案、五週、一個 reviewer，
# 人講比機器擋快）。這支腳本就是全部的部署流程。
#
#   ./deploy/deploy.sh              # 更新程式並重啟
#   ./deploy/deploy.sh --schema     # 一併套用 sql/
#   ./deploy/deploy.sh --seed       # 一併套用 schema 與假資料
set -euo pipefail

APP_DIR=/srv/guildhub
SERVICE=guildhub

cd "$APP_DIR"

echo "== 取得最新程式 =="
git pull --ff-only

echo "== 相依套件 =="
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet \
  fastapi "uvicorn[standard]" pytest pytest-asyncio httpx asyncpg \
  websockets itsdangerous python-dotenv

case "${1:-}" in
  --schema) echo "== 套用 schema ==";       .venv/bin/python tools/apply_sql.py ;;
  --seed)   echo "== 套用 schema 與假資料 =="; .venv/bin/python tools/apply_sql.py --seed ;;
esac

echo "== 測試 =="
# 部署前先跑一次。紅的就不要上 —— 這是唯一的閘門，沒有 CI 幫忙擋。
.venv/bin/python -m pytest -q

echo "== 重啟服務 =="
sudo systemctl restart "$SERVICE"

echo "== 確認起來了 =="
for i in $(seq 1 20); do
  if curl -fs -o /dev/null http://127.0.0.1:8000/health; then
    echo "健康檢查通過（第 ${i} 次嘗試）"
    systemctl is-active "$SERVICE"
    exit 0
  fi
  sleep 1
done

echo "服務在 20 秒內沒有回應 /health"
journalctl -u "$SERVICE" -n 40 --no-pager
exit 1
