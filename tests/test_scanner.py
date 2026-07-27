from pathlib import Path

from src.scanner import scan_files
from src.state import StateStore


def make_code_tree(root: Path) -> None:
    (root / "plsql").mkdir(parents=True)
    (root / "plsql" / "billing.pks").write_text(
        "CREATE OR REPLACE PACKAGE billing AS\n  PROCEDURE calc;\nEND;")
    (root / "plsql" / "billing.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY billing AS\n  PROCEDURE calc IS BEGIN NULL; END;\nEND;")
    (root / "delphi").mkdir()
    (root / "delphi" / "MainForm.pas").write_text("unit MainForm;\ninterface\nend.")
    (root / "readme.txt").write_text("не код — игнорируется")


def test_scan_files_finds_supported_types(tmp_path):
    make_code_tree(tmp_path)
    objects = scan_files(tmp_path)

    assert {o.type for o in objects} == {"PACKAGE_SPEC", "PACKAGE_BODY", "DELPHI_UNIT"}
    assert {o.id for o in objects} == {
        "file:plsql/billing.pks", "file:plsql/billing.pkb", "file:delphi/MainForm.pas"}
    # чексумма стабильна между сканами
    assert [o.checksum for o in objects] == [o.checksum for o in scan_files(tmp_path)]


def test_scan_files_missing_dir_is_empty(tmp_path):
    assert scan_files(tmp_path / "nope") == []


def test_state_diff_incremental(tmp_path):
    make_code_tree(tmp_path)
    store = StateStore(tmp_path / "state" / "autodocs.db")

    # первый скан — всё новое
    diff = store.apply_scan(scan_files(tmp_path))
    assert len(diff.changed) == 3 and not diff.unchanged and not diff.removed

    # без изменений — всё SKIPPED-кандидаты
    diff = store.apply_scan(scan_files(tmp_path))
    assert not diff.changed and len(diff.unchanged) == 3

    # правка одного файла — регенерируется только он (US-007)
    (tmp_path / "plsql" / "billing.pkb").write_text("CREATE OR REPLACE PACKAGE BODY billing AS\nEND;")
    diff = store.apply_scan(scan_files(tmp_path))
    assert [o.id for o in diff.changed] == ["file:plsql/billing.pkb"]
    assert len(diff.unchanged) == 2

    # удаление файла — объект уходит из реестра
    (tmp_path / "delphi" / "MainForm.pas").unlink()
    diff = store.apply_scan(scan_files(tmp_path))
    assert diff.removed == ["file:delphi/MainForm.pas"]

    store.close()


def test_status_updates(tmp_path):
    make_code_tree(tmp_path)
    store = StateStore(tmp_path / "state" / "autodocs.db")
    diff = store.apply_scan(scan_files(tmp_path))
    assert store.counts_by_status() == {"PENDING": 3}

    for o in diff.changed:
        store.set_status(o.id, "SUCCESS")
    assert store.counts_by_status() == {"SUCCESS": 3}

    store.set_status(diff.changed[0].id, "ERROR", "битый исходник")
    assert store.counts_by_status() == {"SUCCESS": 2, "ERROR": 1}
    store.close()
