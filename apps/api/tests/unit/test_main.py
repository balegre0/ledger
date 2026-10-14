lazy import pytest

from ledger_app_api import main


def test_main_serves_the_api_role_and_exits_with_the_server_s_status(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[str] = []

    def serve(role: str) -> int:
        served.append(role)
        return 0

    monkeypatch.setattr(main, "serve", serve)
    assert main.main() == 0
    assert served == ["api"]
