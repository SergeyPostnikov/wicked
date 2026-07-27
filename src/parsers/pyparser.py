"""Парсер Python — импорты через ast, без эвристик.

В calls попадают импортируемые модули за вычетом stdlib: относительные
импорты (from .runner import x) — всегда, абсолютные — если это не стандартная
библиотека. На уровне модулей это и есть граф зависимостей проекта.
Таблицы БД не извлекаются (SQL в строках — вне этой итерации).
"""

import ast
import sys

from src.parsers.base import BaseParser, Deps

_STDLIB = frozenset(sys.stdlib_module_names)


class PyParser(BaseParser):
    types = ("PYTHON_MODULE",)

    def parse(self, content: str, self_name: str = "") -> Deps:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return Deps()  # битый синтаксис — без зависимостей, страница остаётся
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:  # относительный импорт — точно модуль проекта
                    if node.module:
                        modules.add(node.module.split(".")[0])
                    else:  # from . import runner, models
                        modules.update(alias.name for alias in node.names)
                elif node.module:
                    modules.add(node.module.split(".")[0])
        calls = sorted(m for m in modules if m not in _STDLIB and m != self_name)
        return Deps(calls=calls)
