from pathlib import Path

from src.generator import Generator, split_by_procedures
from src.parser import deps_mermaid, parse_plsql_deps
from src.publisher import MkdocsPublisher
from src.scanner import CodeObject

PROMPTS = Path(__file__).parent.parent / "prompts"


def make_obj(content: str, obj_id: str = "file:pkg/billing.pkb") -> CodeObject:
    return CodeObject(id=obj_id, source="file", name="billing", type="PACKAGE_BODY",
                      path="pkg/billing.pkb", checksum="c" * 64, content=content)


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return f"описание #{len(self.calls)}"


# ─── чанкование (US-005) ─────────────────────────────────────────────────────

def test_split_respects_procedure_boundaries():
    src = ("PACKAGE BODY billing AS\n"
           + "".join(f"PROCEDURE p{i} IS BEGIN NULL; END;\n{'-- x' * 20}\n"
                     for i in range(10))
           + "END;")
    chunks = split_by_procedures(src, max_chars=300)
    assert len(chunks) > 1
    assert "".join(chunks) == src  # ничего не потеряно
    for chunk in chunks[1:]:  # каждый чанк (кроме шапки) начинается с процедуры
        assert chunk.lstrip().upper().startswith("PROCEDURE")


def test_split_small_source_is_single_chunk():
    src = "PACKAGE BODY tiny AS END;"
    assert split_by_procedures(src, max_chars=1000) == [src]


def test_generator_single_call_when_fits():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=8192)
    text = gen.describe(make_obj("PACKAGE BODY small AS END;"))
    assert text == "описание #1"
    assert len(llm.calls) == 1


def test_generator_picks_delphi_prompt():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=8192)
    obj = CodeObject(id="file:MainForm.pas", source="file", name="MainForm",
                     type="DELPHI_UNIT", path="MainForm.pas", checksum="c" * 64,
                     content="unit MainForm;\ninterface\nend.")
    gen.describe(obj)
    assert "Delphi" in llm.calls[0][0]  # системный промпт — из delphi_unit.md


def test_generator_delphi_map_reduce_prompts():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=100)
    big = "".join(f"procedure TForm1.Handler{i}(Sender: TObject);\nbegin\nend;\n"
                  for i in range(30))
    obj = CodeObject(id="file:Big.pas", source="file", name="Big",
                     type="DELPHI_UNIT", path="Big.pas", checksum="c" * 64,
                     content=big)
    gen.describe(obj)
    assert all("Delphi" in system for system, _ in llm.calls)


def test_generator_picks_python_prompt():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=8192)
    obj = CodeObject(id="file:app/utils.py", source="file", name="utils",
                     type="PYTHON_MODULE", path="app/utils.py", checksum="c" * 64,
                     content="def helper():\n    return 1\n")
    gen.describe(obj)
    assert "Python-модуль" in llm.calls[0][0]


def test_generator_python_map_reduce_splits_by_def():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=100)
    big = "".join(f"def handler_{i}(x):\n    return x + {i}\n\n"
                  f"class Thing{i}:\n    pass\n\n" for i in range(20))
    obj = CodeObject(id="file:big.py", source="file", name="big",
                     type="PYTHON_MODULE", path="big.py", checksum="c" * 64,
                     content=big)
    gen.describe(obj)
    assert len(llm.calls) > 2  # порезалось на чанки + сводка
    assert all("Python" in system for system, _ in llm.calls)
    # чанки начинаются с границы def/class, а не с середины тела
    for _, user in llm.calls[:-1]:
        body = user.split("]\n", 1)[1]
        assert body.startswith(("def ", "class ", "async def "))


def test_generator_map_reduce_when_large():
    llm = FakeLLM()
    gen = Generator(llm, PROMPTS, context_tokens=100)  # крошечный контекст
    big = "".join(f"PROCEDURE p{i} IS BEGIN NULL; END;\n" for i in range(30))
    gen.describe(make_obj(big))
    assert len(llm.calls) > 2  # N чанков + 1 сводка
    assert "фрагмент 1/" in llm.calls[0][1]


# ─── parser (US-006) ─────────────────────────────────────────────────────────

def test_parse_plsql_deps():
    src = """
    PROCEDURE calc IS
    BEGIN
      SELECT amount INTO v FROM invoices WHERE id = p_id;
      UPDATE accounts SET balance = balance - v;
      tax_pkg.apply(p_id);  -- вызов другого пакета
      billing.internal(p_id);  -- self — исключается
    END;
    """
    deps = parse_plsql_deps(src, self_name="billing")
    assert deps.tables == ["accounts", "invoices"]
    assert deps.calls == ["tax_pkg"]


def test_mermaid_output():
    deps = parse_plsql_deps("SELECT 1 FROM invoices; tax_pkg.apply(1);")
    diagram = deps_mermaid("billing", deps)
    assert diagram.startswith("flowchart LR")
    assert "billing --> tax_pkg" in diagram
    assert "billing --> invoices[(invoices)]" in diagram


# ─── publisher (US-008) ──────────────────────────────────────────────────────

def test_publisher_writes_page_and_commits(tmp_path):
    pub = MkdocsPublisher(tmp_path, PROMPTS / "templates", model="test-model")
    obj = make_obj("PACKAGE BODY billing AS END;")
    deps = parse_plsql_deps("SELECT 1 FROM invoices;")

    page = pub.publish(obj, "Пакет считает счета.", deps)
    assert page == tmp_path / "docs" / "file" / "pkg" / "billing.pkb.md"
    content = page.read_text()
    assert "Пакет считает счета." in content
    assert "<!-- autodocs:llm -->" in content
    assert "`invoices`" in content

    pub.finalize("test")
    assert (tmp_path / "mkdocs.yml").exists()
    index = (tmp_path / "docs" / "index.md").read_text()
    assert "[billing.pkb](file/pkg/billing.pkb.md)" in index
    from git import Repo
    repo = Repo(tmp_path)
    assert not repo.is_dirty(untracked_files=True)
    assert "1 страниц" in repo.head.commit.message


def test_publisher_idempotent_paths(tmp_path):
    pub = MkdocsPublisher(tmp_path, PROMPTS / "templates", model="m")
    oracle_obj = CodeObject(id="oracle:APP.BILLING:PACKAGE BODY", source="oracle",
                            name="BILLING", type="PACKAGE BODY", path="APP.BILLING",
                            checksum="c" * 64, content="x")
    assert pub.page_path(oracle_obj) == \
        tmp_path / "docs" / "oracle" / "APP" / "BILLING.PACKAGE_BODY.md"
