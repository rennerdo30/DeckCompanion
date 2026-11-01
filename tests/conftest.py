from pathlib import Path
import pytest
from deckcompanion.common import Paths


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    from deckcompanion import net
    def deny(*args, **kwargs):
        raise AssertionError("Tests must mock network access")
    monkeypatch.setattr(net, "fetch", deny)
    # A test of the worker supervisor restores only this callable and mocks Popen.


@pytest.fixture
def paths(tmp_path, monkeypatch):
    roots = {"DECKCOMPANION_HOME": tmp_path / "data", "DECKCOMPANION_CACHE": tmp_path / "cache",
             "DECKCOMPANION_CONFIG": tmp_path / "config", "DECKCOMPANION_STEAM_ROOT": tmp_path / "Steam",
             "DECKCOMPANION_COMPAT_TOOLS": tmp_path / "tools", "DECKCOMPANION_RECIPES": Path(__file__).resolve().parents[1] / "recipes"}
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("DECKCOMPANION_STEAM_USER_ID", "123")
    return Paths.env()
