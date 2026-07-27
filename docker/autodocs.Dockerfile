# Autodocs — сервис генерации документации.
# Oracle 11g: python-oracledb в thin mode поддерживает только 12.1+,
# поэтому запекаем Oracle Instant Client (19c умеет коннектиться к 11.2)
# и работаем в thick mode.

FROM python:3.12-slim-bookworm AS base

# libaio — рантайм-зависимость Instant Client
RUN apt-get update \
    && apt-get install -y --no-install-recommends libaio1 curl unzip git \
    && rm -rf /var/lib/apt/lists/*

# ── Oracle Instant Client (basiclite, x64) ──
ARG INSTANTCLIENT_URL=https://download.oracle.com/otn_software/linux/instantclient/instantclient-basiclite-linuxx64.zip
RUN curl -sSL "$INSTANTCLIENT_URL" -o /tmp/ic.zip \
    && unzip -q /tmp/ic.zip -d /opt/oracle \
    && rm /tmp/ic.zip \
    && ln -s /opt/oracle/instantclient_* /opt/oracle/instantclient
ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

# ── Python-зависимости (uv) ──
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Приложение и дефолтные промпты ──
COPY src/ ./src/
# default_prompts копируются в PROMPTS_PATH при первом старте (US-017)
COPY prompts/ ./default_prompts/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

ENV CODE_PATH=/code \
    WIKI_PATH=/wiki \
    PROMPTS_PATH=/prompts \
    STATE_PATH=/state

VOLUME ["/state"]
EXPOSE 8080

ENTRYPOINT ["python", "-m", "src.main"]
