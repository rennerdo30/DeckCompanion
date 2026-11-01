from pathlib import Path
import pytest
from deckcompanion.common import Paths


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    from deckcompanion import net, decky_api
    def deny(*args, **kwargs):
        raise AssertionError("Tests must mock network access")
    monkeypatch.setattr(net, "fetch", deny)
    monkeypatch.setattr(net, "download", deny, raising=False)
    monkeypatch.setattr(decky_api, "HTTPConnection", deny)
    # A test of the worker supervisor restores only this callable and mocks Popen.


@pytest.fixture
def built_plugin(tmp_path, monkeypatch):
    """Release tests use an already-built tiny frontend, never npm or network."""
    import json
    from scripts import build_decky_plugin
    root = tmp_path / "plugin-source"
    for name in ("main.py", "plugin.json", "package.json", "dist/index.js", "THIRD_PARTY_NOTICES.md"):
        path = root / "decky-plugin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": "DeckCompanion", "version": "0.1.0"}) if name.endswith(".json") else "fixture")
    (root / "LICENSE").write_text("fixture")
    api = root / "decky-plugin/node_modules/@decky/api"
    (api / "src").mkdir(parents=True)
    (api / "src/index.ts").write_text("fixture")
    for name in ("LICENSE", "package.json"):
        (api / name).write_text("fixture")
    archive = build_decky_plugin.build(root)
    monkeypatch.setattr(build_decky_plugin, "build", lambda root: archive)
    return archive


@pytest.fixture
def paths(tmp_path, monkeypatch):
    roots = {"DECKCOMPANION_HOME": tmp_path / "data", "DECKCOMPANION_CACHE": tmp_path / "cache",
             "DECKCOMPANION_CONFIG": tmp_path / "config", "DECKCOMPANION_STEAM_ROOT": tmp_path / "Steam",
             "DECKCOMPANION_COMPAT_TOOLS": tmp_path / "tools", "DECKCOMPANION_RECIPES": Path(__file__).resolve().parents[1] / "recipes"}
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("DECKCOMPANION_STEAM_USER_ID", "123")
    return Paths.env()
