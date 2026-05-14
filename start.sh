#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- config ----
export DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/data}"
export WEBHOOK_PORT="${WEBHOOK_PORT:-8000}"
export ILINK_BASE_URL="${ILINK_BASE_URL:-https://ilinkai.weixin.qq.com}"
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

# ---- prepare ----
mkdir -p "$DATA_DIR"

# ---- check dependencies ----
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo ">>> 安装依赖..."
    pip3 install -r requirements.txt
fi

# ---- create admin ----
if [ -n "$ADMIN_USERNAME" ] && [ -n "$ADMIN_PASSWORD" ]; then
    echo ">>> 管理员: $ADMIN_USERNAME (首次启动自动创建)"
fi

echo ">>> 启动 WxClawbotPush (http://0.0.0.0:$WEBHOOK_PORT)"
cd "$SCRIPT_DIR/wxclawbotpush"
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$WEBHOOK_PORT"
