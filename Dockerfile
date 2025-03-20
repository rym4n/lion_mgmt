FROM sanicframework/sanic:24.12-py3.11

RUN apk add --no-cache \
    gcc \
    libc-dev \
    python3-dev \
    make

RUN pip install poetry==2.1.1

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache \
    RY_ENV=PROD

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN poetry install --without dev && rm -rf $POETRY_CACHE_DIR

COPY . ./

ENTRYPOINT ["poetry", "run", "python", "server.py"]
