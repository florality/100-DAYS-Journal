#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c 'import fastapi, uvicorn, PIL' >/dev/null 2>&1; then
  echo "缺少运行依赖，请先执行："
  echo "  python3 -m pip install -r requirements.txt"
  exit 1
fi

PORT="${PORT:-8765}"
echo "一百个今天已启动：http://127.0.0.1:${PORT}"
echo "按 Ctrl+C 停止。"
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port "${PORT}"
