from datetime import timedelta
from pathlib import Path

import platformdirs

from pr_evidence_reviewer.config import Settings


def test_cache_and_data_dirs_default_to_the_per_user_platform_dirs() -> None:
    settings = Settings()

    assert settings.cache_dir == Path(
        platformdirs.user_cache_dir("pr-evidence-reviewer", appauthor=False)
    )
    assert settings.data_dir == Path(
        platformdirs.user_data_dir("pr-evidence-reviewer", appauthor=False)
    )


def test_checkout_retention_defaults_to_two_gib_and_seven_days() -> None:
    settings = Settings()

    assert settings.checkout_cache_max_bytes == 2 * 1024**3
    assert settings.checkout_ttl == timedelta(days=7)


def test_tests_can_point_state_at_a_temporary_directory(tmp_path: Path) -> None:
    settings = Settings(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")

    assert settings.cache_dir == tmp_path / "cache"
    assert settings.data_dir == tmp_path / "data"


def test_building_settings_creates_no_directories(tmp_path: Path) -> None:
    Settings(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")

    assert list(tmp_path.iterdir()) == []
