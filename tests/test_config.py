from pathlib import Path

import pytest

from pothole.config import Settings


def test_defaults_when_env_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "POTHOLE_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)
    monkeypatch.delenv("POTHOLE_STATE")
    monkeypatch.setenv("HOME", str(tmp_path))
    s = Settings.from_env()
    assert s.deployment == "pothole-agent" and s.socrata_app_token is None
    assert s.state == tmp_path / ".cache" / "pothole"


def test_env_values_and_empty_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("POTHOLE_DEPLOYMENT", "")
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok")
    s = Settings.from_env()
    assert (s.azure_endpoint, s.azure_api_key) == ("https://x", "k")
    assert s.deployment == "pothole-agent" and s.socrata_app_token == "tok"
