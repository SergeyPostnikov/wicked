"""Publisher — mkdocs-адаптер (specs.yaml components.publisher, US-008).

Пишет docs/*.md в WIKI_PATH со стабильной иерархией (conventions.wiki_structure):
  docs/oracle/<OWNER>/<NAME>.<TYPE>.md
  docs/file/<относительный/путь>.md
Навигацию mkdocs строит сам по дереву каталогов — nav не генерируем.
Завершение прохода — git-коммит в вики-репозитории.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from git import Actor, InvalidGitRepositoryError, Repo
from jinja2 import Environment, FileSystemLoader

from src.parsers import Deps, deps_mermaid
from src.scanner import CodeObject

log = logging.getLogger("autodocs.publisher")

_MKDOCS_YML = """site_name: Autodocs
theme:
  name: material
markdown_extensions:
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
"""


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)


class MkdocsPublisher:
    def __init__(self, wiki_path: Path, templates_path: Path, model: str):
        self._wiki = wiki_path
        self._docs = wiki_path / "docs"
        self._model = model
        self._env = Environment(loader=FileSystemLoader(templates_path),
                                keep_trailing_newline=True)
        self._written: list[Path] = []

    def prepare(self) -> None:
        """Начало прохода: mkdocs.yml и черновой index.md, если их ещё нет —
        viewer показывает вики уже во время первой генерации."""
        self._docs.mkdir(parents=True, exist_ok=True)
        mkdocs_yml = self._wiki / "mkdocs.yml"
        if not mkdocs_yml.exists():
            mkdocs_yml.write_text(_MKDOCS_YML, encoding="utf-8")
        index = self._docs / "index.md"
        if not index.exists():
            index.write_text(
                "# Документация\n\nИдёт первый проход генерации — страницы "
                "появляются по мере готовности.\n", encoding="utf-8")

    def page_path(self, obj: CodeObject) -> Path:
        if obj.source == "oracle":
            owner, name = obj.path.split(".", 1)
            rel = Path("oracle") / _safe(owner) / f"{_safe(name)}.{_safe(obj.type)}.md"
        else:
            # расширение сохраняем в имени (billing.pks.md ≠ billing.pkb.md)
            rel = Path("file") / (obj.path + ".md")
        return self._docs / rel

    def publish(self, obj: CodeObject, llm_text: str, deps: Deps) -> Path:
        template = self._env.get_template("package_page.md.j2")
        page = template.render(
            object={
                "name": obj.name,
                "schema": obj.path.split(".", 1)[0] if obj.source == "oracle"
                          else str(Path(obj.path).parent),
                "type": obj.type,
                "lines": obj.content.count("\n") + 1,
                "checksum": obj.checksum,
                "tables": deps.tables,
                "calls": deps.calls,
            },
            generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            model=self._model,
            llm_text=llm_text.strip(),
            diagram=deps_mermaid(obj.name, deps),
        )
        path = self.page_path(obj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
        self._written.append(path)
        return path

    def remove(self, obj_path: Path) -> None:
        obj_path.unlink(missing_ok=True)

    def _write_index(self) -> None:
        """docs/index.md — оглавление по дереву страниц. Перестраивается целиком."""
        pages = sorted(p for p in self._docs.rglob("*.md")
                       if p.name != "index.md")
        lines = ["# Документация", "",
                 f"Всего страниц: {len(pages)}", ""]
        current_dir = None
        for p in pages:
            rel = p.relative_to(self._docs)
            if rel.parent != current_dir:
                current_dir = rel.parent
                lines += [f"## {current_dir.as_posix()}", ""]
            lines.append(f"- [{p.stem}]({rel.as_posix()})")
        (self._docs / "index.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")

    def finalize(self, trigger: str) -> None:
        """mkdocs.yml при первом прогоне + git-коммит изменений вики."""
        if not self._written:
            log.info("Страницы не менялись — публикация не требуется")
            return
        self._write_index()

        try:
            repo = Repo(self._wiki)
        except InvalidGitRepositoryError:
            repo = Repo.init(self._wiki, initial_branch="main")
        repo.git.add(A=True)
        if repo.is_dirty(untracked_files=True):
            author = Actor("autodocs", "autodocs@localhost")
            repo.index.commit(
                f"autodocs: {len(self._written)} страниц (триггер: {trigger})",
                author=author, committer=author)
            log.info("Вики: закоммичено страниц — %d", len(self._written))
        self._written.clear()
