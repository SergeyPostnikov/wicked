"""База парсеров зависимостей (specs.yaml components.parser).

Парсер — источник фактов для диаграмм: только статический анализ, не LLM
(US-006). Один парсер обслуживает набор типов объектов (attr `types`);
подбор парсера по типу — в src/parsers/__init__.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Deps:
    tables: list[str] = field(default_factory=list)  # используемые таблицы БД
    calls: list[str] = field(default_factory=list)   # вызываемые пакеты/модули


class BaseParser(ABC):
    types: tuple[str, ...] = ()  # типы объектов CodeObject.type, которые парсер понимает

    @abstractmethod
    def parse(self, content: str, self_name: str = "") -> Deps:
        """Зависимости объекта из исходника. self_name исключается из результата."""


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
