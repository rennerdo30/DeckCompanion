import json
from datetime import datetime
from deckcompanion import cli, decky
from deckcompanion.common import atomic, read_json, write_json


def test_updates_unseen_bounded_and_seen_cli(paths, monkeypatch, capsys):
    from deckcompanion import updates
    monkeypatch.setattr(decky, "homebrew", lambda: paths.data / "homebrew")
    assert updates.summary(paths) == {"changed": [], "available": []}
    for i in range(55):
        updates.record(paths, "engine", "DeckCompanion", str(i), str(i + 1))
    updates.record(paths, "engine", "DeckCompanion", "55", "55")
    assert cli.main(["updates", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["changed"]) == 50
    assert result["changed"][0]["from"] == "5" and result["changed"][-1]["to"] == "55"
    assert set(result["changed"][0]) == {"module", "name", "from", "to", "at"}
    assert datetime.fromisoformat(result["changed"][0]["at"]).tzinfo is not None
    assert updates.summary(paths) == result  # reading does not acknowledge
    assert cli.main(["updates", "--seen", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == result  # returns what was acknowledged
    assert updates.summary(paths)["changed"] == []
    assert len(read_json(paths.data / "updates.json")["entries"]) == 50
    updates.record(paths, "umu", "umu-launcher", None, "1.0")
    assert updates.summary(paths)["changed"][0]["from"] is None


def test_updates_available_reads_cached_data_only(paths, monkeypatch):
    from deckcompanion import updates
    monkeypatch.setattr(decky, "homebrew", lambda: paths.data / "homebrew")
    atomic(decky.homebrew() / "services/PluginLoader", "loader")
    atomic(decky.homebrew() / "services/.loader.version", "v3.3.0")
    root = decky.homebrew() / "plugins/odd-dir"
    write_json(root / "plugin.json", {"name": "ProtonDB Badges"})
    write_json(root / "package.json", {"version": "1.2.0"})
    write_json(paths.cache / "decky-release.json", {"version": "v3.3.1"})
    write_json(paths.cache / "decky-plugin-release.json", {"version": "0.2.0", "sha256": "b" * 64,
               "url": "https://github.com/rennerdo30/DeckCompanion/releases/download/decky-v0.2.0/deckcompanion-decky-0.2.0.zip"})
    write_json(paths.cache / "decky-store.json", {"plugins": {"ProtonDB Badges": {
        "version": "1.3.4", "sha256": "a" * 64, "url": decky.STORE_CDN + "a" * 64 + ".zip"}}})
    # autouse network guard stays active; service queries are forbidden too.
    monkeypatch.setattr(decky.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected process")))
    result = updates.summary(paths)
    assert result["available"] == [
        {"module": "decky-loader", "name": "Decky Loader", "installed": "v3.3.0", "latest": "v3.3.1"},
        {"module": "decky-plugins", "name": "ProtonDB Badges", "installed": "1.2.0", "latest": "1.3.4"},
        {"module": "deckcompanion-plugin", "name": "DeckCompanion", "installed": None, "latest": "0.2.0"}]
    assert updates.summary(paths, seen=True)["available"] == result["available"]
