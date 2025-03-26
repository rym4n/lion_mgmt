FROM python:3.11-slim as builder

RUN apt-get update && apt-get install -y \
    gcc \
    libc-dev \
    python3-dev \
    make \
    libaio-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry==2.1.1

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN touch README.md

RUN --mount=type=cache,target=$POETRY_CACHE_DIR poetry install --without dev --no-root

RUN python compile.py

FROM python:3.11-slim as runtime

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder dist/ ./
COPY server.py ./

ENTRYPOINT ["python", "server.py"]
