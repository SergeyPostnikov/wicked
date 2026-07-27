"""Конфигурация сервиса — все параметры из env, схема в specs.yaml (config)."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM ──
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5-coder:14b"
    llm_context_tokens: int = 8192
    llm_timeout_sec: int = 600

    # ── Источники ──
    code_path: Path = Path("/code")
    oracle_dsn: str = ""  # пусто — источник Oracle отключён
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_schemas: str = ""  # список схем через запятую

    # ── Триггеры ──
    schedule_cron: str = "0 3 * * *"  # пусто — расписание отключено
    git_poll_sec: int = 0  # 0 — поллинг отключён
    webhook_secret: str = ""  # пусто — webhook отключён
    webhook_port: int = 8080

    # ── Публикация ──
    wiki_type: str = "mkdocs"
    wiki_path: Path = Path("/wiki")
    prompts_path: Path = Path("/prompts")
    default_prompts_path: Path = Path("/app/default_prompts")

    # ── Система ──
    state_path: Path = Path("/state")
    max_retries: int = 3
    log_level: str = "INFO"

    @property
    def oracle_enabled(self) -> bool:
        return bool(self.oracle_dsn)

    @property
    def oracle_schema_list(self) -> list[str]:
        return [s.strip() for s in self.oracle_schemas.split(",") if s.strip()]


settings = Settings()
