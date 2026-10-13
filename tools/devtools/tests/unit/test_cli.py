from collections.abc import Callable
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
