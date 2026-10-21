from collections.abc import Callable
from typing import Final
lazy from pathlib import Path

lazy import pytest

from ledger_devtools import cli, generate, protos
from ledger_devtools.cli import main
from ledger_devtools.protos import ProtocFailedError

type Editor = Callable[[str, str, str], None]
type Writer = Callable[[str, str, tuple[str, ...]], Path]


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


ORPHAN: Final = (
    "generated, but no manifest or contract implies it any more: run `uv run ledger-devtools sync`"
)


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


VAULT: Final = """
[package]
name = "vault"
kind = "module"
summary = "Keys."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
"""
PING: Final = 'syntax = "proto3";\n\npackage ledger.vault.v1;\n\nmessage Ping {\n  string id = 1;\n}\n'
CODE: Final = "modules/vault/src/ledger_vault/adapters/grpc/_generated"


def test_a_proto_of_no_module_stops_sync(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proto = tree / "contracts/proto/ledger/vault/v1/vault.proto"
    proto.parent.mkdir(parents=True)
    proto.write_text(PING, encoding="utf-8")
    assert run(capsys, "sync", "--root", str(tree)) == (
        1,
        [
            (
                "contracts/proto/ledger/vault/v1/vault.proto: "
                "no module 'vault' (modules/vault/manifest.toml) to generate its gRPC code into"
            )
        ],
        "",
    )
    assert not (tree / ".importlinter").exists()


def test_grpc_code_follows_its_contract(
    tree: Path, capsys: pytest.CaptureFixture[str], write: Writer
) -> None:
    write("modules/vault", VAULT, ("adapters",))
    proto = tree / "contracts/proto/ledger/vault/v1/vault.proto"
    proto.parent.mkdir(parents=True)
    proto.write_text(PING, encoding="utf-8")
    code, written, _ = run(capsys, "sync", "--root", str(tree))
    assert code == 0
    assert f"wrote {CODE}/v1/vault_pb2.pyi" in written
    assert run(capsys, "sync", "--root", str(tree)) == (0, [], "")
    proto.write_text(PING.replace("string id = 1;", "string id = 1;\n  string at = 2;"), encoding="utf-8")
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    assert (code, lines) == (
        1,
        [
            f"{CODE}/v1/{name}: stale, run `uv run ledger-devtools sync`"
            for name in ("vault_pb2.py", "vault_pb2.pyi")
        ],
    )
    proto.unlink()
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    names = ("__init__.py", "v1/__init__.py", "v1/vault_pb2.py", "v1/vault_pb2.pyi")
    no_omit = "modules/vault/pyproject.toml: stale, run `uv run ledger-devtools sync`"  # nothing to omit
    assert (code, lines) == (1, [no_omit, *(f"{CODE}/{name}: {ORPHAN}" for name in names)])
    run(capsys, "sync", "--root", str(tree))
    assert [path for path in (tree / CODE).rglob("*") if path.is_file()] == []
    assert run(capsys, "check", "--root", str(tree)) == (0, [], "")


def test_a_generated_file_out_of_its_sync_is_an_orphan(
    tree: Path, capsys: pytest.CaptureFixture[str], write: Writer
) -> None:
    write("modules/vault", VAULT, ("adapters",))
    proto = tree / "contracts/proto/ledger/vault/v1/vault.proto"
    proto.parent.mkdir(parents=True)
    proto.write_text(PING, encoding="utf-8")
    run(capsys, "sync", "--root", str(tree))
    nested = tree / CODE / "v1/deeper/stray.py"
    nested.parent.mkdir()
    nested.write_text((tree / CODE / "v1/vault_pb2.py").read_text(encoding="utf-8"), encoding="utf-8")
    assert run(capsys, "check", "--root", str(tree)) == (1, [f"{CODE}/v1/deeper/stray.py: {ORPHAN}"], "")


REFUSAL: Final = "ledger/vault/v1/vault.proto:1:1: refused"


def test_protoc_refusing_what_check_found_sound_ends_the_command(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(root: Path) -> dict[Path, bytes]:
        del root
        raise ProtocFailedError(REFUSAL)

    monkeypatch.setattr(generate, "grpc_code", refused)
    assert run(capsys, "sync", "--root", str(tree)) == (
        1,
        [f"contracts/proto: protoc could not generate the gRPC code: {REFUSAL}"],
        "",
    )


def test_without_protoc_s_plugins_the_contracts_cannot_be_checked(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, write: Writer
) -> None:
    write("modules/vault", VAULT, ("adapters",))
    proto = tree / "contracts/proto/ledger/vault/v1/vault.proto"
    proto.parent.mkdir(parents=True)
    proto.write_text(PING, encoding="utf-8")
    monkeypatch.setattr(protos, "_PLUGINS", ("absent",))
    assert run(capsys, "check", "--root", str(tree)) == (
        2,
        [],
        "protoc-gen-absent is not installed beside this interpreter: run `uv sync --all-packages`\n",
    )


UNINSTALLED: Final = """
[package]
name = "zzbilling"
kind = "module"
summary = "Bills, not installed."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[http]
mounted_in = ["api"]
prefixes = ["/v1/zzbills"]
"""


def test_check_needs_every_module_the_api_mounts_installed(
    tree: Path, capsys: pytest.CaptureFixture[str], write: Writer
) -> None:
    write("modules/zzbilling", UNINSTALLED, ())
    code, lines, error = run(capsys, "check", "--root", str(tree))
    assert (code, lines) == (2, [])
    assert error == (
        "modules/zzbilling/manifest.toml: ledger-zzbilling is not installed, so its OpenAPI document "
        "cannot be made: run `uv sync --all-packages`\n"
    )


def test_sync_writes_what_installs_a_new_module_then_asks_for_the_install(
    tree: Path, capsys: pytest.CaptureFixture[str], write: Writer
) -> None:
    """A new mounted module cannot be installed before sync writes its pyproject: sync writes it first."""
    write("modules/zzbilling", UNINSTALLED, ())
    code, lines, error = run(capsys, "sync", "--root", str(tree))
    assert code == 2
    assert "wrote modules/zzbilling/pyproject.toml" in lines
    assert error == (
        "modules/zzbilling/manifest.toml: ledger-zzbilling is not installed: installed packages are behind: "
        "run `uv sync --all-packages`, then sync again\n"
    )
    assert (tree / "modules/zzbilling/pyproject.toml").is_file()
    assert run(capsys, "sync", "--root", str(tree)) == (2, [], error)  # nothing more to write meanwhile


def test_sync_writes_every_other_file_before_it_makes_the_snapshots(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app the snapshots come from imports what sync writes: a stale ``_manifest.py`` would break it."""
    manifest = tree / "modules/identity/src/ledger_identity/_manifest.py"
    seen: list[bool] = []

    def snapshots(root: Path) -> dict[Path, str]:
        seen.append((root / "modules/identity/src/ledger_identity/_manifest.py").is_file())
        return {}

    monkeypatch.setattr(cli, "openapi_documents", snapshots)
    assert not manifest.exists()
    assert run(capsys, "sync", "--root", str(tree))[0] == 0
    assert seen == [True]


def test_check_makes_no_snapshot_while_another_file_is_stale(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def snapshots(root: Path) -> dict[Path, str]:
        raise AssertionError(root)

    monkeypatch.setattr(cli, "openapi_documents", snapshots)
    code, lines, _ = run(capsys, "check", "--root", str(tree))
    assert code == 1
    assert ".importlinter: stale, run `uv run ledger-devtools sync`" in lines


def test_snapshots_that_cannot_be_made_end_the_command(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def snapshots(root: Path) -> dict[Path, str]:
        del root
        failure = "InvalidPackError: identity could not be loaded"
        raise cli.SnapshotsUnavailableError(failure)

    monkeypatch.setattr(cli, "openapi_documents", snapshots)
    code, _, error = run(capsys, "sync", "--root", str(tree))
    assert code == 2
    assert (
        error == "the OpenAPI snapshots could not be made: InvalidPackError: identity could not be loaded\n"
    )


def test_the_snapshots_are_made_in_a_fresh_interpreter_or_say_why_not(
    tree: Path, capsys: pytest.CaptureFixture[str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    run(capsys, "sync", "--root", str(tree))
    made = cli.openapi_documents(tree)
    assert made == {
        Path("modules/identity/openapi.json"): (tree / "modules/identity/openapi.json").read_text(
            encoding="utf-8"
        )
    }
    documents, failure = cli.snapshots_in(str(tree))
    assert (set(documents), failure) == ({"modules/identity/openapi.json"}, None)
    empty = tmp_path_factory.mktemp("empty")
    assert cli.snapshots_in(str(empty)) == ({}, f"WorkspaceError: no manifest.toml under {empty}")
    with pytest.raises(cli.SnapshotsUnavailableError, match="WorkspaceError"):
        cli.openapi_documents(empty)
