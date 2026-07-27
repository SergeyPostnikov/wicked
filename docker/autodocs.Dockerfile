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
# Запинен на 19c: это последняя ветка клиента, поддерживающая сервер 11.2
# (у заказчика Oracle 11; latest = 23ai с 11.2 не соединяется — ORA-28040).
ARG INSTANTCLIENT_URL=https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-linux.x64-19.28.0.0.0dbru.zip
RUN curl -sSL "$INSTANTCLIENT_URL" -o /tmp/ic.zip \
    && unzip -q /tmp/ic.zip -d /opt/oracle \
    && rm /tmp/ic.zip \
    && ln -s /opt/oracle/instantclient_* /opt/oracle/instantclient
ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

# /wiki монтируется с хоста с чужим uid — иначе git внутри контейнера
# отказывается работать (dubious ownership)
RUN git config --system --add safe.directory '*'

# ── Python-зависимости (uv) ──
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Приложение и дефолтные промпты ──
COPY LICENSE ./
COPY src/ ./src/
# default_prompts копируются в PROMPTS_PATH при первом старте (US-017)
COPY prompts/ ./default_prompts/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

ENV CODE_PATH=/code \
    WIKI_PATH=/wiki \
    PROMPTS_PATH=/prompts \
    STATE_PATH=/state

# Непривилегированный пользователь: волюмы (/wiki, /prompts, /state) остаются
# доступны хосту, а не root-овые. uid/gid переопределяются под владельца
# волюмов на хосте (compose: user: "${UID}:${GID}").
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g "$APP_GID" autodocs 2>/dev/null || true \
    && useradd -m -u "$APP_UID" -g "$APP_GID" autodocs
USER autodocs
ENV HOME=/home/autodocs

VOLUME ["/state"]
EXPOSE 8080

ENTRYPOINT ["python", "-m", "src.main"]
