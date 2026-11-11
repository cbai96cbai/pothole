import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pothole.llm.tokens import count_text_tokens


@pytest.fixture(scope="session", autouse=True)
def tokenizer() -> None:
    """Setup, before any test runs offline: tiktoken fetches its data file once, then caches it."""
    count_text_tokens("")


@pytest.fixture(autouse=True)
def offline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests run offline unless marked live or live_api: any network connection fails the test."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("live_api"):
        yield
        return
    attempts: list[object] = []

    def refuse(real: Callable[[socket.socket, Any], Any]) -> Callable[[socket.socket, Any], Any]:
        def call(self: socket.socket, address: Any) -> Any:
            if self.family == socket.AF_UNIX:
                return real(self, address)
            attempts.append(address)
            raise OSError(f"network access in a test not marked live or live_api: {address}")

        return call

    monkeypatch.setattr(socket.socket, "connect", refuse(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse(socket.socket.connect_ex))
    yield
    assert not attempts, f"the test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def isolated_state(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """No test touches the real state directory; only tests marked live reach the real spend
    tally, because they spend real money and it must be counted."""
    state = tmp_path / "state"
    monkeypatch.setenv("POTHOLE_STATE", str(state))
    if not request.node.get_closest_marker("live"):
        monkeypatch.setattr("pothole.budget.tally.DEFAULT_PATH", tmp_path / "spend.jsonl")
    return state


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch checkout for the daily run: the pinned spec and the committed data files."""
    from daily_kit import fields_of
    from webdata.site_repo import scratch_repo

    from pothole.socrata import meta

    monkeypatch.setattr(meta, "fields", fields_of)
    return scratch_repo(tmp_path / "repo")
