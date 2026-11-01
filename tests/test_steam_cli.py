import json
from pathlib import Path
import pytest
import vdf
from deckcompanion import cli, steam
from deckcompanion.common import Error, atomic, save_state, state


def test_discovery_across_libraries(paths, tmp_path):
    other = tmp_path / "SD card"
    atomic(paths.steam / "steamapps/libraryfolders.vdf", vdf.dumps({"libraryfolders": {"0": {"path": str(paths.steam)}, "1": {"path": str(other)}}}))
    atomic(other / "steamapps/appmanifest_2300320.acf", vdf.dumps({"AppState": {"appid": "2300320", "name": "FS25", "installdir": "Farming Simulator 25"}}))
    assert steam.libraries(paths) == [paths.steam, other]
    assert steam.installed(paths)["2300320"]["path"] == str(other / "steamapps/common/Farming Simulator 25")
    for name in ("GE-Proton9-9", "GE-Proton11-11", "GE-Proton11-2"):
        atomic(paths.tools / name / "proton", "fake")
    atomic(other / "steamapps/common/Proton 10.0/proton", "fake")
    assert [p["name"] for p in steam.proton(paths)] == ["GE-Proton11-11", "GE-Proton11-2", "Proton 10.0", "GE-Proton9-9"]


def invoke(capsys, *args):
    code = cli.main([*args, "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out)


def test_mapping_round_trip_and_backups(paths, monkeypatch, capsys):
    monkeypatch.setattr(steam, "running", lambda: False)
    doc = {"other": "untouched"}
    previous = {"name": "proton_10", "config": "custom", "priority": "100"}
    steam.mappings(doc).update({"2300320": previous, "99": {"name": "another"}})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    assert invoke(capsys, "enable", "2300320")[1]["pending"] == "enable"
    assert steam.read_vdf(steam.mapping_path(paths)) == doc
    assert invoke(capsys, "apply")[1] == {"applied": [{"appid": "2300320", "action": "enable"}], "steam_restarted": False}
    updated = steam.read_vdf(steam.mapping_path(paths))
    assert updated["other"] == "untouched"
    assert steam.mappings(updated)["2300320"]["name"] == "DeckCompanion"
    assert steam.mappings(updated)["99"] == {"name": "another"}
    invoke(capsys, "disable", "2300320")
    invoke(capsys, "apply")
    assert steam.read_vdf(steam.mapping_path(paths)) == doc
    assert len(list(steam.mapping_path(paths).parent.glob("*.bak"))) == 2


def test_foreign_mapping_not_removed(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    doc = {}
    steam.mappings(doc)["42"] = {"name": "foreign"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    save_state(paths, {"games": {"42": {"pending": "disable", "enabled": True}}})
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == doc


def test_binary_shortcut_round_trip(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    path = steam.shortcut_path(paths)
    existing = {"appid": 123, "AppName": "Keep", "Exe": "other", "tags": {"0": "Favorites"}}
    atomic(path, vdf.binary_dumps({"shortcuts": {"0": existing}}))
    first = steam.shortcut_bytes(paths, path)
    steam.guarded_write(path, first)
    assert steam.shortcut_bytes(paths, path) == first
    shortcuts = vdf.binary_loads(first)["shortcuts"]
    assert shortcuts["0"] == existing
    assert shortcuts["1"]["appid"] < 0
    assert shortcuts["1"]["AppName"] == "DeckCompanion"
    assert vdf.binary_loads(steam.shortcut_bytes(paths, path, remove=True))["shortcuts"] == {"0": existing}


def test_refuse_running_and_shutdown_timeout(paths, monkeypatch):
    monkeypatch.setattr(steam.sys, "platform", "win32")  # In-process dev behavior stays intact.
    monkeypatch.setattr(steam, "running", lambda: True)
    with pytest.raises(Error, match="Close Steam"):
        steam.guarded_write(paths.steam / "config/config.vdf", b"bad")
    save_state(paths, {"games": {"42": {"pending": "enable"}}})
    with pytest.raises(Error, match="restart-steam"):
        steam.apply(paths)
    calls = []
    monkeypatch.setattr(steam, "run", lambda cmd, timeout: calls.append((cmd, timeout)))
    ticks = iter([0, 11])
    monkeypatch.setattr(steam.time, "monotonic", lambda: next(ticks))
    with pytest.raises(Error, match="Close Steam"):
        steam.apply(paths, restart=True)
    assert calls == [(["steam", "-shutdown"], 10)]
    assert not steam.mapping_path(paths).exists()
    assert state(paths)["games"]["42"]["pending"] == "enable"


def test_apply_restart_and_transaction_rollback(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    calls = []
    monkeypatch.setattr(steam, "run", lambda cmd, timeout: calls.append(cmd))
    monkeypatch.setattr(steam, "start", lambda: calls.append(["steam", "-silent"]))
    save_state(paths, {"games": {"42": {"pending": "enable"}}, "shortcut_pending": True})
    real_write = steam.guarded_write
    def fail_second(path, payload):
        if path.name == "shortcuts.vdf":
            raise OSError("disk failure")
        real_write(path, payload)
    monkeypatch.setattr(steam, "guarded_write", fail_second)
    with pytest.raises(OSError):
        steam.apply(paths)
    assert not steam.mapping_path(paths).exists()
    assert state(paths)["games"]["42"]["pending"] == "enable"
    monkeypatch.setattr(steam, "guarded_write", real_write)
    assert steam.apply(paths, restart=True)["steam_restarted"] is True
    assert calls == [["steam", "-silent"]]


def test_cli_shapes_and_errors(paths, monkeypatch, capsys):
    monkeypatch.setattr(steam, "running", lambda: False)
    code, result = invoke(capsys, "games")
    assert code == 0
    fs = next(g for g in result["games"] if g["appid"] == "2300320")
    assert set(fs) == {"appid", "name", "installed", "has_recipe", "enabled", "pending", "recipe_version", "mods", "last_launch", "art", "proton", "last_played",
                       "takeover", "previous_mapping", "valve_recommendation", "options"}
    assert fs["options"] == {"proton": True, "takeover": True}
    assert fs["last_launch"] is None and fs["enabled"] is False
    assert set(fs["mods"][0]) == {"id", "name", "version", "enabled", "installed_version", "state", "state_message"}
    assert invoke(capsys, "mod", "2300320", "fs25-deck-mode", "--off")[1]["mods"][0]["enabled"] is False
    assert invoke(capsys, "mod", "2300320", "missing", "--on")[0] == 1
    atomic(paths.data / "logs/2300320-last.log", "".join(f"{n}\n" for n in range(230)))
    detail = invoke(capsys, "game", "2300320")[1]
    assert isinstance(detail["log_tail"], list) and len(detail["log_tail"]) == 200
    assert detail["log_tail"][0] == "30"
    assert "notes" in detail
    assert set(invoke(capsys, "status")[1]) == {"version", "install_path", "steam_running", "default_tool", "pending", "proton", "last_apply",
                                               "takeover_all", "takeover_counts"}
    assert invoke(capsys, "status")[1]["last_apply"] is None
    code, error = invoke(capsys, "enable", "../bad")
    assert code == 1 and error["error"]["code"] == "invalid_appid"
    assert invoke(capsys, "missing")[1]["error"]["code"] == "invalid_arguments"


def test_shortcut_user_picks_most_recent_account(paths, monkeypatch):
    import os
    monkeypatch.delenv("DECKCOMPANION_STEAM_USER_ID")
    for ident, mtime in (("0", 300), ("73549515", 200), ("702852743", 100)):
        config = paths.steam / "userdata" / ident / "config"
        config.mkdir(parents=True)
        (config / "localconfig.vdf").write_text('"UserLocalConfigStore"\n{\n}\n')
        os.utime(config / "localconfig.vdf", (mtime, mtime))
    assert steam.shortcut_path(paths).parent.parent.name == "73549515"


def test_last_played_from_localconfig(paths):
    # Library sort uses Steam's LastPlayed (owner, Deck 2026-11-01).
    config = paths.steam / "userdata/123/config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "localconfig.vdf").write_text(
        '"UserLocalConfigStore"\n{\n "Software"\n {\n  "Valve"\n  {\n   "Steam"\n   {\n    "apps"\n    {\n'
        '     "2300320"\n     {\n      "LastPlayed"   "1793530000"\n     }\n     "480"\n     {\n      "LastPlayed"   "0"\n     }\n'
        '    }\n   }\n  }\n }\n}\n', encoding="utf-8")
    assert steam.last_played(paths) == {"2300320": 1793530000}
    assert cli.game(paths, "2300320")["last_played"] == 1793530000
    assert cli.game(paths, "480")["last_played"] is None
