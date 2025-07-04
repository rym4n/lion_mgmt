#!/bin/bash

set -e

/usr/local/bin/juicefs status "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10"
if [[ $? -ne 0 ]]; then
  /usr/local/bin/juicefs format --storage redis --bucket redis://${RY_REDIS_HOST}:${RY_REDIS_PORT}/9 --secret-key "${RY_REDIS_PASS}" "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10" lionfs
fi

/usr/local/bin/juicefs mount --max-uploads=50 --cache-dir /fs_cache --cache-size 512 --entry-cache 300 --attr-cache 300 -d "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10" /app/tools

sanic server:app --host 0.0.0.0 --port 8000 -w 4 --no-access-logs --no-motd
