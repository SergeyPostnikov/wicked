"""Реестр парсеров: тип объекта → парсер. Нет парсера — нет диаграммы (Deps())."""

from src.parsers.base import BaseParser, Deps, deps_mermaid
from src.parsers.plsqlparser import PlsqlParser
from src.parsers.pyparser import PyParser

_PARSERS: dict[str, BaseParser] = {
    obj_type: parser
    for parser in (PlsqlParser(), PyParser())
    for obj_type in parser.types
}


def get_parser(obj_type: str) -> BaseParser | None:
    return _PARSERS.get(obj_type)


__all__ = ["BaseParser", "Deps", "deps_mermaid", "get_parser"]
