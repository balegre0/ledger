from collections.abc import Callable
from typing import Final
lazy from pathlib import Path

lazy import pytest

from ledger_devtools.cli import main

type Editor = Callable[[str, str, str], None]


def run(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, list[str], str]:
    code = main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_sync_writes_every_target_once(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, written, _ = run(capsys, "sync", "--root", str(tree))
    assert code == 0
    assert "wrote .importlinter" in written
    assert "wrote modules/identity/src/ledger_identity/_manifest.py" in written
    assert (tree / "modules/identity/pyproject.toml").read_text(encoding="utf-8").startswith("# GENERATED")
    assert run(capsys, "sync", "--root", str(tree)) == (0, [], "")
    assert run(capsys, "check", "--root", str(tree)) == (0, [], "")


def test_check_names_a_stale_file(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "sync", "--root", str(tree))
    (tree / "apps/api/pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tree / ".importlinter").unlink()
    assert run(capsys, "check", "--root", str(tree)) == (
        1,
        [
            ".importlinter: stale, run `uv run ledger-devtools sync`",
            "apps/api/pyproject.toml: stale, run `uv run ledger-devtools sync`",
        ],
        "",
    )


def test_check_lists_manifest_problems(tree: Path, capsys: pytest.CaptureFixture[str], edit: Editor) -> None:
    run(capsys, "sync", "--root", str(tree))
    edit("packages/platform", 'every = "24h"', 'cron = "0 3 * * *"')
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    assert code == 1
    assert lines[0].startswith(
        "packages/platform/manifest.toml: schedule 'platform.purge_done_jobs' uses cron"
    )


def test_sync_writes_nothing_while_a_manifest_is_wrong(
    tree: Path, capsys: pytest.CaptureFixture[str], edit: Editor
) -> None:
    edit("modules/identity", 'packages = ["kernel", "platform"]', 'packages = ["kernel", "ghost"]')
    code, lines, _ = run(capsys, "sync", "--root", str(tree))
    assert (code, lines) == (
        1,
        ["modules/identity/manifest.toml: dependencies.packages names 'ghost', which is not a package"],
    )
    assert not (tree / ".importlinter").exists()


def test_a_tree_without_manifests_is_unreadable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines, error = run(capsys, "check", "--root", str(tmp_path))
    assert (code, lines) == (2, [])
    assert error.startswith("no manifest.toml under ")


def test_the_root_defaults_to_the_current_directory(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tree)
    assert run(capsys, "check")[0] == 1


ORPHAN: Final = "generated, but no manifest implies it any more: run `uv run ledger-devtools sync`"


def test_a_package_that_drops_its_schema_loses_its_manifest_module(
    tree: Path, capsys: pytest.CaptureFixture[str], edit: Editor
) -> None:
    run(capsys, "sync", "--root", str(tree))
    edit("packages/platform", '[data]\nschema = "platform"\n', "")
    manifest_module = "packages/platform/src/ledger_platform/_manifest.py"
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    assert code == 1
    assert f"{manifest_module}: {ORPHAN}" in lines
    code, lines, _ = run(capsys, "sync", "--root", str(tree))
    assert code == 0
    assert f"deleted {manifest_module}" in lines
    assert not (tree / manifest_module).exists()
    assert run(capsys, "check", "--root", str(tree)) == (0, [], "")


def test_generated_files_nothing_implies_are_deleted_and_hand_written_ones_kept(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "sync", "--root", str(tree))
    (tree / "apps/api/manifest.toml").unlink()
    stray = tree / "packages/kernel/src/ledger_kernel/_manifest.py"
    stray.write_text('"""Hand-written."""\n', encoding="utf-8")
    hand_written = tree / "apps/legacy/pyproject.toml"
    hand_written.parent.mkdir()
    hand_written.write_text("[project]\n", encoding="utf-8")
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    assert code == 1
    assert f"apps/api/pyproject.toml: {ORPHAN}" in lines
    assert f"packages/kernel/src/ledger_kernel/_manifest.py: {ORPHAN}" in lines
    assert not any(line.startswith("apps/legacy") for line in lines)
    run(capsys, "sync", "--root", str(tree))
    assert not (tree / "apps/api/pyproject.toml").exists()
    assert not stray.exists()
    assert hand_written.exists()


def test_check_names_repository_problems_that_sync_leaves_alone(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "deploy/local").mkdir(parents=True)
    (tree / "deploy/local/compose.yaml").write_text(
        "services:\n  db:\n    image: postgres:18.7\n", encoding="utf-8"
    )
    code, written, _ = run(capsys, "sync", "--root", str(tree))
    assert code == 0
    assert written
    assert run(capsys, "check", "--root", str(tree)) == (
        1,
        ["deploy/local/compose.yaml: image 'postgres:18.7' is not pinned in deploy/local/images.toml"],
        "",
    )
