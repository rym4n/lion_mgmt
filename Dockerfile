FROM mirror.gcr.io/library/python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 AS builder

ARG TARGETARCH

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
    UV_HTTP_TIMEOUT=300 \
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

RUN --mount=type=cache,id=lion-mgmt-ccache-${TARGETARCH},target=/root/.ccache,sharing=locked \
    nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file core/
RUN --mount=type=cache,id=lion-mgmt-ccache-${TARGETARCH},target=/root/.ccache,sharing=locked \
    nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file libs/
RUN --mount=type=cache,id=lion-mgmt-ccache-${TARGETARCH},target=/root/.ccache,sharing=locked \
    nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file models/
RUN --mount=type=cache,id=lion-mgmt-ccache-${TARGETARCH},target=/root/.ccache,sharing=locked \
    nuitka --mode=package --nofollow-imports --output-dir=dist --jobs=4 --remove-output --no-pyi-file services/

FROM mirror.gcr.io/library/python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 AS runtime

ARG TARGETARCH
ARG VECTOR_VERSION=0.50.0
ARG VECTOR_AMD64_SHA256=5ecc335a2d2a1ff73c8408b5d979b6e238f5663ba8803ea5054ca8aa464dd410
ARG VECTOR_ARM64_SHA256=d2732550f5e35ab09200e87d4965391b9f90510bc0055cfe37548fe4c12bc891

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update -y \
    && apt-get install -y curl libc-dev python3-dev libaio-dev \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/vector tools

RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) VECTOR_ARCH="x86_64"; VECTOR_SHA256="$VECTOR_AMD64_SHA256" ;; \
        arm64) VECTOR_ARCH="aarch64"; VECTOR_SHA256="$VECTOR_ARM64_SHA256" ;; \
        *) echo "Unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    VECTOR_PACKAGE="vector-${VECTOR_ARCH}-unknown-linux-gnu"; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
        "https://packages.timber.io/vector/${VECTOR_VERSION}/vector-${VECTOR_VERSION}-${VECTOR_ARCH}-unknown-linux-gnu.tar.gz" \
        -o /tmp/vector.tar.gz \
    && tar -xzf /tmp/vector.tar.gz -C /tmp \
    && install -m 0755 \
        "/tmp/${VECTOR_PACKAGE}/bin/vector" \
        /usr/bin/vector \
    && printf '%s  %s\n' "$VECTOR_SHA256" /usr/bin/vector | sha256sum -c - \
    && rm -rf /tmp/vector.tar.gz "/tmp/${VECTOR_PACKAGE}"

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY migrations ./migrations
COPY pyproject.toml ./
COPY settings ./settings
COPY server.py ./
COPY docker-entrypoint.sh ./
COPY services/asset_management/builtin_catalog/ ./services/asset_management/builtin_catalog/
COPY services/inspection/inspection_report_template.docx ./services/inspection/inspection_report_template.docx
COPY services/inspection/inspection_template_model.xlsx ./services/inspection/inspection_template_model.xlsx
COPY services/network_topology/schemas/ ./services/network_topology/schemas/
COPY services/network_topology/registries/ ./services/network_topology/registries/

RUN python -c "from services.network_topology.contract_smoke import main; main()"
RUN chmod +x docker-entrypoint.sh

# 在镜像构建时初始化 aerich 配置（不需要数据库连接）
RUN aerich init -t settings.TORTOISE || true

ENTRYPOINT ["./docker-entrypoint.sh"]
