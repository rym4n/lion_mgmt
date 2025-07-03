#!/bin/bash

set -e

export PATH="/bin:/usr/bin:/sbin:/usr/sbin"

juicefs status "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10"
if [[ $? -ne 0 ]]; then
  juicefs format --storage redis --bucket redis://${RY_REDIS_HOST}:${RY_REDIS_PORT}/9 --secret-key "${RY_REDIS_PASS}" "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10" lionfs
fi

juicefs info /app/tools
if [[ $? -ne 0 ]]; then
  juicefs mount --max-uploads=50 --entry-cache 300 --attr-cache 300 -d "redis://:${RY_REDIS_PASS}@${RY_REDIS_HOST}:${RY_REDIS_PORT}/10" /app/tools
fi

sanic server:app --host 0.0.0.0 --port 8000 -w 4 --no-access-logs --no-motd
