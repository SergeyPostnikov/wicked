"""Generator — описания объектов через LLM (specs.yaml components.generator).

Детерминированный пайплайн без tool calling (conventions.llm_usage):
объект влезает в контекст — один вызов по промпту package.md;
не влезает — map-reduce (US-005): чанки по границам процедур → chunk.md,
затем сводка из описаний чанков → summary.md.
"""

import logging
import re
from pathlib import Path

from src.llm import OllamaClient
from src.scanner import CodeObject

log = logging.getLogger("autodocs.generator")

# Граница чанка — объявление процедуры/функции/класса верхнего уровня.
# Для PL/SQL и Delphi — PROCEDURE/FUNCTION, для Python — def/class без отступа.
_PROC_RE = re.compile(r"^\s*(?:PROCEDURE|FUNCTION)\s+[A-Za-z_][\w$#]*",
                      re.IGNORECASE | re.MULTILINE)
_PY_RE = re.compile(r"^(?:async\s+def|def|class)\s+[A-Za-z_]\w*", re.MULTILINE)
_SPLIT_RE_BY_TYPE = {"PYTHON_MODULE": _PY_RE}  # остальные типы — _PROC_RE

# Оценка: ~3 байта на токен для кода; половина контекста — под ответ модели
_CHARS_PER_TOKEN = 3

# Семейство промптов по типу объекта: (основной, чанк, сводка)
_PLSQL_PROMPTS = ("package.md", "chunk.md", "summary.md")
_DELPHI_PROMPTS = ("delphi_unit.md", "delphi_chunk.md", "delphi_summary.md")
_PYTHON_PROMPTS = ("python_module.md", "python_chunk.md", "python_summary.md")
_PROMPTS_BY_TYPE = {
    "DELPHI_UNIT": _DELPHI_PROMPTS,
    "DELPHI_FORM": _DELPHI_PROMPTS,
    "PYTHON_MODULE": _PYTHON_PROMPTS,
}  # всё остальное (PL/SQL из файлов и ALL_SOURCE) — _PLSQL_PROMPTS


def split_by_procedures(content: str, max_chars: int,
                        pattern: re.Pattern = _PROC_RE) -> list[str]:
    """Режет исходник по границам процедур и пакует куски в чанки до max_chars.

    Кусок длиннее max_chars (гигантская процедура) остаётся одним чанком —
    лучше обрезка на стороне модели, чем разрыв посреди логики.
    """
    starts = [m.start() for m in pattern.finditer(content)] or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    pieces = [content[a:b] for a, b in zip(starts, starts[1:] + [len(content)])]

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


class Generator:
    def __init__(self, llm: OllamaClient, prompts_path: Path, context_tokens: int):
        self._llm = llm
        self._prompts = prompts_path
        # половина контекста под вход, половина под ответ
        self._max_chars = context_tokens * _CHARS_PER_TOKEN // 2

    def _prompt(self, name: str) -> str:
        f = self._prompts / name
        if not f.is_file():
            raise FileNotFoundError(f"промпт не найден: {f}")
        return f.read_text(encoding="utf-8")

    def describe(self, obj: CodeObject) -> str:
        """Текст описания объекта. Retryable-ошибки LLM уже отработаны клиентом."""
        main_p, chunk_p, summary_p = _PROMPTS_BY_TYPE.get(obj.type, _PLSQL_PROMPTS)
        if len(obj.content) <= self._max_chars:
            return self._llm.generate(self._prompt(main_p), obj.content)

        # map-reduce (US-005)
        chunks = split_by_procedures(obj.content, self._max_chars,
                                     _SPLIT_RE_BY_TYPE.get(obj.type, _PROC_RE))
        log.info("Объект %s не влезает в контекст — %d чанков", obj.id, len(chunks))
        chunk_prompt = self._prompt(chunk_p)
        partials = [
            self._llm.generate(chunk_prompt,
                               f"[фрагмент {i + 1}/{len(chunks)}]\n{chunk}")
            for i, chunk in enumerate(chunks)
        ]
        joined = "\n\n---\n\n".join(partials)
        return self._llm.generate(self._prompt(summary_p), joined)
