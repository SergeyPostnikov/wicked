"""Клиент к Ollama (specs.yaml glossary.backend).

Только локальный endpoint (EXCL-001). Ретраи по specs.yaml state_machine:
таймаут/5xx/обрыв — retryable, прочее — нет.
"""

import logging
import time

import httpx

from src.config import Settings

log = logging.getLogger("autodocs.llm")


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class OllamaClient:
    def __init__(self, settings: Settings):
        self._url = settings.ollama_url.rstrip("/")
        self._model = settings.ollama_model
        self._num_ctx = settings.llm_context_tokens
        self._timeout = settings.llm_timeout_sec
        self._max_retries = settings.max_retries

    def generate(self, system: str, user: str) -> str:
        """Один запрос к /api/chat с ретраями retryable-ошибок."""
        last: LLMError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._chat(system, user)
            except LLMError as e:
                last = e
                if not e.retryable:
                    raise
                log.warning("LLM retryable-ошибка (попытка %d/%d): %s",
                            attempt + 1, self._max_retries, e)
                time.sleep(min(2 ** attempt, 30))
        raise LLMError(f"ретраи исчерпаны: {last}", retryable=False)

    def _chat(self, system: str, user: str) -> str:
        try:
            resp = httpx.post(
                f"{self._url}/api/chat",
                json={
                    "model": self._model,
                    "stream": False,
                    "options": {"num_ctx": self._num_ctx},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise LLMError(f"сеть/таймаут: {e}", retryable=True) from e
        if resp.status_code >= 500:
            raise LLMError(f"HTTP {resp.status_code}", retryable=True)
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False)
        try:
            return resp.json()["message"]["content"]
        except (KeyError, ValueError) as e:
            raise LLMError(f"неожиданный ответ: {e}", retryable=False) from e
