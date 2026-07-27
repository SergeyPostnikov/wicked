"""Parser — статический анализ зависимостей (specs.yaml components.parser).

Факты для диаграмм берутся только отсюда, не из LLM (US-006).
Пока — регэксп-эвристика по PL/SQL; полноценный разбор — следующая итерация.
"""

import re
from dataclasses import dataclass, field

# Таблицы: FROM/JOIN/INTO/UPDATE/DELETE FROM <ident>
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+([A-Za-z_][\w$#]*)",
    re.IGNORECASE)
# Вызовы пакетов: <pkg>.<proc>( — двухуровневые идентификаторы перед скобкой
_CALL_RE = re.compile(r"\b([A-Za-z_][\w$#]*)\s*\.\s*[A-Za-z_][\w$#]*\s*\(")

# Ключевые слова, ложно матчащиеся как таблица/пакет
_NOISE = {"dual", "table", "select", "where", "the", "loop", "if", "then",
          "dbms_output", "dbms_lob", "dbms_sql", "utl_file", "to_char", "to_date"}


@dataclass
class Deps:
    tables: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


def parse_plsql_deps(content: str, self_name: str = "") -> Deps:
    """Таблицы и вызываемые пакеты из текста PL/SQL (без комментариев и строк)."""
    text = _strip_comments(content)
    tables = {m.group(1).lower() for m in _TABLE_RE.finditer(text)}
    calls = {m.group(1).lower() for m in _CALL_RE.finditer(text)}
    self_lower = self_name.lower()
    tables -= _NOISE
    calls = {c for c in calls if c not in _NOISE and not c.startswith("dbms_")
             and not c.startswith("utl_") and c != self_lower}
    return Deps(tables=sorted(tables), calls=sorted(calls))


def _strip_comments(content: str) -> str:
    content = re.sub(r"--[^\n]*", "", content)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"'[^']*'", "''", content)
    return content


def deps_mermaid(name: str, deps: Deps) -> str:
    """Mermaid-диаграмма связей объекта (specs.yaml glossary.diagram)."""
    if not deps.tables and not deps.calls:
        return ""
    lines = ["flowchart LR", f"    {name}[{name}]"]
    for c in deps.calls:
        lines.append(f"    {name} --> {c}[{c}]")
    for t in deps.tables:
        lines.append(f"    {name} --> {t}[({t})]")
    return "\n".join(lines)
