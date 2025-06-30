FROM python:3.11-slim AS builder

RUN sed -i 's/http:\/\/deb.debian.org/https:\/\/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y \
    gcc \
    libc-dev \
    python3-dev \
    make \
    libaio-dev \
    ccache

RUN pip install uv==0.7.4 nuitka==2.7.7 -i https://mirrors.aliyun.com/pypi/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/usr/lib/ccache:$PATH" \
    CCACHE_DIR="$HOME/.ccache" \
    CC="ccache gcc" \
    CXX="ccache g++"

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . ./

RUN sed -i "s|AIOE_LICENSE_KEY|$(cat LICKEY | tr -d '\n')|g" libs/utils.py

RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=8 --remove-output --no-pyi-file core/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=8 --remove-output --no-pyi-file libs/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=8 --remove-output --no-pyi-file models/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=8 --remove-output --no-pyi-file services/

FROM timberio/vector:0.47.0-debian AS vector

FROM python:3.11-slim AS runtime

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ="Asia/Shanghai" \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN sed -i 's/http:\/\/deb.debian.org/https:\/\/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y curl libc-dev python3-dev libaio-dev && rm -rf /var/lib/apt/lists/* && mkdir /var/lib/vector

COPY --from=vector /usr/bin/vector /usr/bin/vector
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY settings ./settings
COPY server.py ./

ENTRYPOINT ["sanic", "server:app", "--host", "0.0.0.0", "--port", "8000", "-w", "4", "--no-access-logs", "--no-motd"]
