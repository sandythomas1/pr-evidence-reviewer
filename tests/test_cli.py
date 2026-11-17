import os
import sys
from pathlib import Path

import pytest

from pr_evidence_reviewer.cli import main
from pr_evidence_reviewer.config import Settings
from tests.checkout.fakes import FakeArchiveSource, publish


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


def settings_in(tmp_path: Path) -> Settings:
    return Settings(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")


def seed_cache(tmp_path: Path) -> None:
    publish(tmp_path / "cache", FakeArchiveSource({"a.py": b"a = 1\n"}))
    packet = tmp_path / "data" / "packets" / "k" / "p.json"
    packet.parent.mkdir(parents=True)
    packet.write_text("{}")


def test_cache_clear_deletes_checkouts_and_keeps_packets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_cache(tmp_path)

    assert main(["cache", "clear"], base_settings=settings_in(tmp_path)) == 0

    out = capsys.readouterr().out
    assert "Deleted 1 saved repository copy" in out
    assert "packets" not in out
    assert os.listdir(tmp_path / "cache" / "checkouts") == []
    assert (tmp_path / "data" / "packets" / "k" / "p.json").exists()


def test_cache_clear_with_packets_deletes_saved_evidence_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_cache(tmp_path)

    assert main(["cache", "clear", "--packets"], base_settings=settings_in(tmp_path)) == 0

    out = capsys.readouterr().out
    assert "Deleted 1 saved evidence packet" in out
    assert not (tmp_path / "data" / "packets").exists()


def test_cache_clear_on_an_empty_cache_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["cache", "clear"], base_settings=settings_in(tmp_path)) == 0

    assert "Deleted 0 saved repository copies" in capsys.readouterr().out


def test_cache_clear_prints_no_repository_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_cache(tmp_path)

    main(["cache", "clear", "--packets"], base_settings=settings_in(tmp_path))

    captured = capsys.readouterr()
    assert "acme" not in captured.out + captured.err
    assert "shop" not in captured.out + captured.err


@pytest.mark.skipif(sys.platform == "win32", reason="directory symlinks need admin on Windows")
def test_cache_clear_refuses_a_linked_cache_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "checkouts").mkdir(parents=True)
    (tmp_path / "cache").symlink_to(elsewhere, target_is_directory=True)

    assert main(["cache", "clear"], base_settings=settings_in(tmp_path)) != 0

    assert "link" in capsys.readouterr().err
    assert (elsewhere / "checkouts").exists()
