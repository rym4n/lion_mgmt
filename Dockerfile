FROM python:3.11-slim AS builder

ARG TARGETARCH

RUN apt update -y && apt install -y \
    gcc \
    libc-dev \
    python3-dev \
    make \
    libaio-dev \
    curl \
    unzip \
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

RUN curl -L "https://dl.hongcloud.net/installer/chrome-linux64.zip" -o /tmp/chrome.zip && unzip /tmp/chrome.zip -d /opt
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

FROM timberio/vector:0.50.0-debian AS vector

FROM python:3.11-slim AS runtime

ENV VIRTUAL_ENV=/app/.venv \
    RY_ENV=PROD \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt update -y \
    && apt install -y curl libc-dev python3-dev libaio-dev \
       fonts-wqy-zenhei fonts-wqy-microhei bubblewrap \
       libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
       libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
       libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /var/lib/vector \
    && mkdir tools

COPY --from=vector /usr/bin/vector /usr/bin/vector
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /app/dist/ ./
COPY --from=builder /opt/chrome-linux64 /root/.agent-browser/browsers/chrome-linux64
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
