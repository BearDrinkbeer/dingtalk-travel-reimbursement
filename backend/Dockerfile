FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

FROM python:3.13.15-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1 \
    PADDLE_PDX_CACHE_HOME=/tmp/expense/.paddlex \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TMPDIR=/tmp/expense/.spool \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends --yes \
        libgl1 \
        libglib2.0-0t64 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
ARG INSTALL_OCR=true
RUN if [ "$INSTALL_OCR" = "true" ]; then \
        uv sync --frozen --no-dev --no-install-project --extra ocr; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

COPY app ./app

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN groupadd --gid 10001 expense \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin expense \
    && chmod 0755 /app/docker-entrypoint.sh \
    && mkdir -p /app/data /app/staging /tmp/expense/.spool /opt/expense/models \
    && chown expense:expense /app/data /tmp/expense /tmp/expense/.spool /app/staging \
    && chmod 0700 /app/data /app/staging /tmp/expense /tmp/expense/.spool \
    && chmod -R a-w /app/app /app/migrations /app/alembic.ini /app/docker-entrypoint.sh

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
