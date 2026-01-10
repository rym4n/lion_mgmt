FROM python:3.11-slim AS builder

RUN apt update -y && apt install -y \
    gcc \
    libc-dev \
    python3-dev \
    make \
    libaio-dev \
    ccache

RUN pip install uv==0.9.22 nuitka==2.8.9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/usr/lib/ccache:$PATH" \
    CCACHE_DIR="/root/.ccache" \
    CCACHE_MAXSIZE="2G" \
    CCACHE_COMPRESS="true" \
    CFLAGS="-fPIC -O3"

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . ./

RUN sed -i "s|AIOE_LICENSE_KEY|$(cat LICKEY | tr -d '\n')|g" libs/utils.py

RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file core/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file libs/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file models/
RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file services/

FROM timberio/vector:0.50.0-debian AS vector

FROM python:3.11-slim AS runtime

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt update -y \
    && apt install -y curl libc-dev python3-dev libaio-dev \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /var/lib/vector \
    && mkdir tools

COPY --from=vector /usr/bin/vector /usr/bin/vector
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY pyproject.toml ./
COPY settings ./settings
COPY server.py ./
