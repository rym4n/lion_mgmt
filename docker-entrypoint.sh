#!/bin/bash
set -e

echo "[Entrypoint] Starting lion-mgmt service..."

# 等待数据库就绪
echo "[Entrypoint] Waiting for database to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if /app/.venv/bin/python -c "
import asyncio
from tortoise import Tortoise
from settings import TORTOISE

async def check_db():
    try:
        await Tortoise.init(config=TORTOISE)
        await Tortoise.close_connections()
        return True
    except Exception as e:
        print(f'Database not ready: {e}')
        return False

result = asyncio.run(check_db())
exit(0 if result else 1)
" 2>/dev/null; then
        echo "[Entrypoint] Database is ready!"
        break
    fi

    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "[Entrypoint] ERROR: Database connection timeout after ${MAX_RETRIES} attempts"
        exit 1
    fi

    echo "[Entrypoint] Database not ready, retrying in 2 seconds... ($RETRY_COUNT/$MAX_RETRIES)"
    sleep 2
done

# 自动执行数据库迁移
echo "[Entrypoint] Running database migrations..."
/app/.venv/bin/aerich upgrade
echo "[Entrypoint] Database migrations completed successfully"

# 启动应用：透传 compose CMD（默认回退到 python server.py）
if [ "$#" -eq 0 ]; then
    echo "[Entrypoint] No command provided, falling back to: python server.py"
    exec /app/.venv/bin/python server.py
fi
echo "[Entrypoint] Starting application: $*"
exec "$@"
