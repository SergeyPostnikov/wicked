"""Точка входа autodocs.

Один процесс (specs.yaml deployment.services_logical: 1):
  - при старте копирует дефолтные промпты в PROMPTS_PATH, если тот пуст (US-017);
  - планировщик по SCHEDULE_CRON (US-009);
  - HTTP: /health, /status, POST /webhook (US-010);
  - глобальный lock — один проход одновременно, повторный триггер = SKIP
    (specs.yaml conventions.locking).

Сам пайплайн (scanner → parser → generator → publisher) пока заглушка.
"""

import hmac
import logging
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from src.config import settings
from src.generator import Generator
from src.llm import LLMError, OllamaClient
from src.parsers import Deps, get_parser
from src.publisher import MkdocsPublisher
from src.scanner import scan_all
from src.state import StateStore

log = logging.getLogger("autodocs")

_run_lock = threading.Lock()
_last_run: dict = {"started_at": None, "finished_at": None, "trigger": None,
                   "scanned": 0, "changed": 0, "removed": 0,
                   "processed": 0, "skipped": 0, "errors": 0}


def ensure_prompts() -> None:
    """Копирует дефолтные промпты в PROMPTS_PATH при первом старте (US-017)."""
    dst = settings.prompts_path
    dst.mkdir(parents=True, exist_ok=True)
    if any(dst.iterdir()):
        log.info("Промпты уже есть в %s — дефолты не копирую", dst)
        return
    src = settings.default_prompts_path
    if not src.is_dir():
        log.warning("Дефолтные промпты не найдены в %s", src)
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)
    log.info("Дефолтные промпты скопированы в %s", dst)


def run_pipeline(trigger: str) -> None:
    """Один проход генерации. Повторный запуск при активном проходе — SKIP."""
    if not _run_lock.acquire(blocking=False):
        log.info("Проход уже идёт — запуск по триггеру «%s» пропущен", trigger)
        return
    try:
        _last_run.update(started_at=datetime.now(UTC).isoformat(),
                         finished_at=None, trigger=trigger,
                         scanned=0, changed=0, removed=0,
                         processed=0, skipped=0, errors=0)
        log.info("Проход начат (триггер: %s)", trigger)

        store = StateStore(settings.state_path / "autodocs.db")
        try:
            objects, source_errors = scan_all(settings)
            diff = store.apply_scan(objects)
            _last_run.update(scanned=len(objects), changed=len(diff.changed),
                             removed=len(diff.removed), skipped=len(diff.unchanged),
                             errors=source_errors)
            for o in diff.unchanged:
                store.set_status(o.id, "SKIPPED")

            generator = Generator(OllamaClient(settings), settings.prompts_path,
                                  settings.llm_context_tokens)
            publisher = MkdocsPublisher(settings.wiki_path,
                                        settings.prompts_path / "templates",
                                        settings.ollama_model)
            publisher.prepare()
            for o in diff.changed:
                if not o.content.strip():
                    # пустой исходник (__init__.py и т.п.) — нечего документировать
                    store.set_status(o.id, "SKIPPED")
                    _last_run["skipped"] += 1
                    continue
                store.set_status(o.id, "RUNNING")
                try:
                    parser = get_parser(o.type)
                    deps = parser.parse(o.content, o.name) if parser else Deps()
                    llm_text = generator.describe(o)
                    publisher.publish(o, llm_text, deps)
                    store.set_status(o.id, "SUCCESS")
                    _last_run["processed"] += 1
                except (LLMError, OSError) as e:
                    # изоляция ошибок: объект в ERROR, проход продолжается (US-011)
                    log.error("Объект %s: %s", o.id, e)
                    store.set_status(o.id, "ERROR", str(e))
                    _last_run["errors"] += 1
            publisher.finalize(trigger)
        except Exception:
            # сбой этапа прохода — фиксируем и не роняем сервис (US-011)
            log.exception("Проход аварийно завершён")
            _last_run["errors"] += 1
        finally:
            store.close()

        _last_run["finished_at"] = datetime.now(UTC).isoformat()
        log.info("Проход завершён: scanned=%s changed=%s removed=%s "
                 "processed=%s skipped=%s errors=%s",
                 _last_run["scanned"], _last_run["changed"], _last_run["removed"],
                 _last_run["processed"], _last_run["skipped"], _last_run["errors"])
    finally:
        _run_lock.release()


def build_scheduler() -> BackgroundScheduler | None:
    if not settings.schedule_cron:
        log.info("SCHEDULE_CRON пуст — расписание отключено")
        return None
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_pipeline, CronTrigger.from_crontab(settings.schedule_cron),
                      args=["schedule"], id="schedule")
    scheduler.start()
    log.info("Расписание активно: %s", settings.schedule_cron)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ensure_prompts()
    settings.state_path.mkdir(parents=True, exist_ok=True)
    scheduler = build_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="autodocs", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    return {"running": _run_lock.locked(), "last_run": _last_run}


@app.post("/webhook")
async def webhook(request: Request, tasks: BackgroundTasks) -> dict:
    """Триггер по событию репозитория (US-010). Токен — заголовок X-Autodocs-Token."""
    if not settings.webhook_secret:
        raise HTTPException(status_code=404, detail="Webhook отключён")
    token = request.headers.get("X-Autodocs-Token", "")
    if not hmac.compare_digest(token, settings.webhook_secret):
        raise HTTPException(status_code=403, detail="Неверный токен")
    tasks.add_task(run_pipeline, "webhook")
    return {"accepted": True}


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=settings.webhook_port, log_config=None)


if __name__ == "__main__":
    main()
