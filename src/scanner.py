"""Scanner — обходит источники и строит реестр объектов с чексуммами.

Два источника (specs.yaml components.scanner):
  - примонтированный волюм с кодом (US-002): файлы по расширениям;
  - Oracle ALL_SOURCE (US-003): пакеты/процедуры/функции/триггеры по схемам.

Scanner только собирает объекты — сравнение с прошлым состоянием и решение
«что регенерировать» делает StateStore (US-007).
"""

import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from src.config import Settings

log = logging.getLogger("autodocs.scanner")

# Расширение → тип объекта (US-002, US-019)
EXTENSION_TYPES = {
    ".pks": "PACKAGE_SPEC",
    ".pkb": "PACKAGE_BODY",
    ".sql": "SQL",
    ".pas": "DELPHI_UNIT",
    ".dfm": "DELPHI_FORM",
    ".py": "PYTHON_MODULE",
}

# Типы ALL_SOURCE, которые документируем (вьюхи — отдельно, их нет в ALL_SOURCE)
ORACLE_SOURCE_TYPES = ("PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER")


@dataclass(frozen=True)
class CodeObject:
    id: str        # стабильный ключ: "file:<relpath>" | "oracle:<owner>.<name>:<type>"
    source: str    # file | oracle
    name: str      # имя файла без расширения / имя объекта БД
    type: str      # PACKAGE_SPEC, DELPHI_UNIT, PACKAGE BODY, ...
    path: str      # relpath в волюме / OWNER.NAME в БД
    checksum: str  # sha256 исходника
    content: str   # исходный текст


def _checksum(text: str) -> str:
    return sha256(text.encode("utf-8", errors="replace")).hexdigest()


def scan_files(code_path: Path) -> list[CodeObject]:
    """Обход волюма с кодом. Неподдерживаемые типы игнорируются без ошибок (US-002)."""
    if not code_path.is_dir():
        log.warning("CODE_PATH %s не существует или не каталог — источник пропущен", code_path)
        return []
    objects: list[CodeObject] = []
    for f in sorted(code_path.rglob("*")):
        obj_type = EXTENSION_TYPES.get(f.suffix.lower())
        if obj_type is None or not f.is_file():
            continue
        rel = f.relative_to(code_path).as_posix()
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:  # нечитаемый файл — ERROR объекта, не прохода (US-011)
            log.error("Не удалось прочитать %s: %s", rel, e)
            continue
        objects.append(CodeObject(
            id=f"file:{rel}",
            source="file",
            name=f.stem,
            type=obj_type,
            path=rel,
            checksum=_checksum(content),
            content=content,
        ))
    log.info("Волюм %s: найдено объектов — %d", code_path, len(objects))
    return objects


def scan_oracle(settings: Settings) -> list[CodeObject]:
    """Выгрузка исходников из ALL_SOURCE (US-003). Только SELECT (EXCL-002)."""
    import oracledb

    try:
        # Oracle 11g требует thick mode (Instant Client запечён в образ)
        oracledb.init_oracle_client()
    except Exception as e:  # уже инициализирован или клиент не найден — попробуем как есть
        log.debug("init_oracle_client: %s", e)

    conn = oracledb.connect(
        user=settings.oracle_user,
        password=settings.oracle_password,
        dsn=settings.oracle_dsn,
    )
    objects: list[CodeObject] = []
    try:
        with conn.cursor() as cur:
            schemas = settings.oracle_schema_list
            binds = ",".join(f":s{i}" for i in range(len(schemas)))
            types = ",".join(f"'{t}'" for t in ORACLE_SOURCE_TYPES)
            cur.execute(
                f"""SELECT owner, name, type, text
                    FROM all_source
                    WHERE owner IN ({binds}) AND type IN ({types})
                    ORDER BY owner, name, type, line""",
                {f"s{i}": s for i, s in enumerate(schemas)},
            )
            current: tuple | None = None
            lines: list[str] = []

            def flush() -> None:
                if current is None:
                    return
                owner, name, obj_type = current
                content = "".join(lines)
                objects.append(CodeObject(
                    id=f"oracle:{owner}.{name}:{obj_type}",
                    source="oracle",
                    name=name,
                    type=obj_type,
                    path=f"{owner}.{name}",
                    checksum=_checksum(content),
                    content=content,
                ))

            for owner, name, obj_type, text in cur:
                key = (owner, name, obj_type)
                if key != current:
                    flush()
                    current, lines = key, []
                lines.append(text or "")
            flush()
    finally:
        conn.close()
    log.info("Oracle %s: найдено объектов — %d", settings.oracle_dsn, len(objects))
    return objects


def scan_all(settings: Settings) -> tuple[list[CodeObject], int]:
    """Обход всех настроенных источников.

    Возвращает (объекты, количество ошибок источников). Ошибка одного
    источника не валит проход (US-011) — остальные сканируются.
    """
    objects: list[CodeObject] = []
    errors = 0

    try:
        objects.extend(scan_files(settings.code_path))
    except Exception:
        log.exception("Сбой сканирования волюма %s", settings.code_path)
        errors += 1

    if settings.oracle_enabled:
        try:
            objects.extend(scan_oracle(settings))
        except Exception:
            log.exception("Сбой сканирования Oracle %s", settings.oracle_dsn)
            errors += 1

    return objects, errors
