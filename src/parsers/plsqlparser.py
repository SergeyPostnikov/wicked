"""Парсер PL/SQL — регэксп-эвристика по таблицам и вызовам пакетов.

Полноценный разбор — следующая итерация; ложные срабатывания фильтруются
списком шума.
"""

import re

from src.parsers.base import BaseParser, Deps

# Таблицы: FROM/JOIN/INTO/UPDATE/DELETE FROM <ident>
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+([A-Za-z_][\w$#]*)",
    re.IGNORECASE)
# Вызовы пакетов: <pkg>.<proc>( — двухуровневые идентификаторы перед скобкой
_CALL_RE = re.compile(r"\b([A-Za-z_][\w$#]*)\s*\.\s*[A-Za-z_][\w$#]*\s*\(")

# Ключевые слова, ложно матчащиеся как таблица/пакет
_NOISE = {"dual", "table", "select", "where", "the", "loop", "if", "then",
          "dbms_output", "dbms_lob", "dbms_sql", "utl_file", "to_char", "to_date"}


class PlsqlParser(BaseParser):
    types = ("PACKAGE_SPEC", "PACKAGE_BODY", "SQL",
             "PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER")

    def parse(self, content: str, self_name: str = "") -> Deps:
        text = _strip_comments(content)
        tables = {m.group(1).lower() for m in _TABLE_RE.finditer(text)}
        calls = {m.group(1).lower() for m in _CALL_RE.finditer(text)}
        tables -= _NOISE
        calls = {c for c in calls if c not in _NOISE and not c.startswith("dbms_")
                 and not c.startswith("utl_") and c != self_name.lower()}
        return Deps(tables=sorted(tables), calls=sorted(calls))


def _strip_comments(content: str) -> str:
    content = re.sub(r"--[^\n]*", "", content)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"'[^']*'", "''", content)
    return content
