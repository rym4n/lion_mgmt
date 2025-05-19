FROM python:3.11-slim as builder

RUN apt-get update && apt-get install -y \
    gcc \
    libc-dev \
    python3-dev \
    make \
    libaio-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv==0.7.4 nuitka==2.7.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . ./

RUN sed -i "s|AIOE_LICENSE_KEY|$(cat LICKEY | tr -d '\n')|g" libs/utils.py

#RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file core/
#RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file libs/
#RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file models/
#RUN nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file services/

FROM python:3.11-slim as runtime

RUN apt-get update && apt-get install -y curl

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY settings ./settings
COPY server.py ./

ENTRYPOINT ["sanic", "server:app", "--host", "0.0.0.0", "--port", "8000", "--fast", "--no-access-logs", "--no-motd"]
