import logging
import logging.config

from pydantic import SecretStr, TypeAdapter
lazy import pytest

from ledger_platform.config.settings import LogFormat, Settings
from ledger_platform.runtime import SERVER_LOGGERS, serving_logs

URL = "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger"


def test_the_server_s_loggers_have_no_handler_of_their_own_and_reach_the_app_s() -> None:
    assert serving_logs(Settings(environment="test", database_url=SecretStr(URL)), service="ledger-api") == {
        "loggers": {name: {"handlers": [], "propagate": True, "level": "INFO"} for name in SERVER_LOGGERS}
    }
    assert SERVER_LOGGERS == ("_granian", "granian.access")


def test_a_server_record_is_a_line_of_the_app_s_format(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(environment="test", database_url=SecretStr(URL), log_format=LogFormat.JSON)
    config = serving_logs(settings, service="ledger-api")
    # what Granian does with it: its own configuration, the "loggers" section replaced by ours
    logging.config.dictConfig({"version": 1, "disable_existing_loggers": False, **config})
    logging.getLogger("_granian").info("Starting granian")
    record = TypeAdapter(dict[str, object]).validate_json(capsys.readouterr().out.strip().splitlines()[-1])
    assert (record["event"], record["service"], record["logger"]) == (
        "Starting granian",
        "ledger-api",
        "_granian",
    )
