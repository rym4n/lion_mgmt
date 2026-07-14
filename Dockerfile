FROM public.ecr.aws/docker/library/python:3.11-slim@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6 AS builder

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

FROM public.ecr.aws/docker/library/python:3.11-slim@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6 AS runtime

ARG TARGETARCH
ARG VECTOR_VERSION=0.50.0

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

RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) VECTOR_ARCH=x86_64; VECTOR_SHA256=951ceb14f2382c1438696552745221c5584d6895f9aa2b0e12ff0e30271d4b0e ;; \
        arm64) VECTOR_ARCH=aarch64; VECTOR_SHA256=ed73245a88638962093b9b5eb92ba3e17681a7b641e26eac674d07b137a605b8 ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    VECTOR_TARBALL="/tmp/vector-${VECTOR_VERSION}.tar.gz"; \
    curl -fL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 300 \
        "https://github.com/vectordotdev/vector/releases/download/v${VECTOR_VERSION}/vector-${VECTOR_VERSION}-${VECTOR_ARCH}-unknown-linux-gnu.tar.gz" \
        -o "${VECTOR_TARBALL}"; \
    echo "${VECTOR_SHA256}  ${VECTOR_TARBALL}" | sha256sum -c -; \
    tar -xOf "${VECTOR_TARBALL}" "./vector-${VECTOR_ARCH}-unknown-linux-gnu/bin/vector" > /usr/bin/vector; \
    chmod 0755 /usr/bin/vector; \
    rm -f "${VECTOR_TARBALL}"

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY migrations ./migrations
COPY pyproject.toml ./
COPY settings ./settings
COPY server.py ./
COPY docker-entrypoint.sh ./
COPY services/inspection/inspection_report_template.docx ./services/inspection/inspection_report_template.docx
COPY services/inspection/inspection_template_model.xlsx ./services/inspection/inspection_template_model.xlsx


RUN chmod +x docker-entrypoint.sh

# 在镜像构建时初始化 aerich 配置（不需要数据库连接）
RUN aerich init -t settings.TORTOISE || true

ENTRYPOINT ["./docker-entrypoint.sh"]

# 安装 LibreOffice Writer 和中文字体，用于docx转pdf使用
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
      libreoffice-writer \
      fonts-noto-cjk && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*
