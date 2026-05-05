#!/usr/bin/env bash
# start.sh — 加奈 Bot 啟動腳本
# 使用方式（Git Bash）：bash start.sh

set -e

PYTHON="/c/Users/User/miniconda3/envs/ml/python.exe"
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo "  加奈 Bot 啟動中..."
echo "  Python：$PYTHON"
echo "  目錄：$BOT_DIR"
echo "========================================="

# 確認 Python 存在
if [ ! -f "$PYTHON" ]; then
    echo "[錯誤] 找不到 Python：$PYTHON"
    echo "請確認 conda 環境路徑是否正確"
    exit 1
fi

# 確認 .env 存在
if [ ! -f "$BOT_DIR/.env" ]; then
    echo "[錯誤] 找不到 .env 檔案：$BOT_DIR/.env"
    exit 1
fi

cd "$BOT_DIR"

# 初始化 DB（若是第一次執行或 DB 為空）
DB_PATH="$BOT_DIR/data/kana.db"
if [ ! -f "$DB_PATH" ] || [ ! -s "$DB_PATH" ]; then
    echo "[初始化] DB 不存在或為空，執行初始化..."
    "$PYTHON" status.py --reset
fi

echo "[啟動] $(date '+%Y-%m-%d %H:%M:%S')"
exec "$PYTHON" src/bot.py
