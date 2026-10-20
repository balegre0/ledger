import pytest

from ledger_app_signer import main


@pytest.mark.parametrize("status", [0, 3])
def test_main_serves_the_signer_role_and_exits_with_the_server_s_status(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    served: list[str] = []

    def serve(role: str) -> int:
        served.append(role)
        return status

    monkeypatch.setattr(main, "serve", serve)
    assert main.main() == status
    assert served == ["signer"]
