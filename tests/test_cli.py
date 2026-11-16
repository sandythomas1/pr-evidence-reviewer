import pytest

from pr_evidence_reviewer.cli import main
from pr_evidence_reviewer.config import Settings


class RecordingServer:
    def __init__(self) -> None:
        self.settings: Settings | None = None

    def __call__(self, settings: Settings) -> None:
        self.settings = settings


def test_serve_defaults_to_loopback(capsys: pytest.CaptureFixture[str]) -> None:
    server = RecordingServer()

    assert main(["serve"], server=server) == 0

    assert server.settings == Settings(host="127.0.0.1", port=8765)
    assert "Warning" not in capsys.readouterr().err


def test_serve_accepts_host_and_port() -> None:
    server = RecordingServer()

    main(["serve", "--host", "localhost", "--port", "9000"], server=server)

    assert server.settings == Settings(host="localhost", port=9000)


def test_serve_warns_when_bound_beyond_loopback(capsys: pytest.CaptureFixture[str]) -> None:
    main(["serve", "--host", "192.168.1.20"], server=RecordingServer())

    assert "reachable from other machines" in capsys.readouterr().err


@pytest.mark.parametrize("port", ["0", "70000", "abc"])
def test_serve_rejects_invalid_port(port: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["serve", "--port", port], server=RecordingServer())

    assert exit_info.value.code == 2


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([], server=RecordingServer())

    assert exit_info.value.code == 2
