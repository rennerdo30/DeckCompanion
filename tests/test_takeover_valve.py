"""Synthetic appinfo and Steam-only fixtures; no device, process or network access."""
import importlib
import os
from pathlib import Path
import struct
import pytest
import vdf
from deckcompanion import cli, install, launch, recipes, steam, update
from deckcompanion.common import atomic, save_state, state


@pytest.fixture(autouse=True)
def no_steam_processes(monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(steam.subprocess, "Popen", lambda *a, **k: pytest.fail("No real process in these fixtures"))


def invoke(paths, *args):
    return cli.execute(cli.parser().parse_args([*args, "--json"]), paths)


def profile(runtime="proton-stable", oslist="windows", **extra):
    return {"common": {"type": "game", "oslist": oslist, "steam_deck_compatibility": {
        "configuration": {"recommended_runtime": runtime, "requires_h264": "1"}}}, **extra}


def appinfo_file(paths, apps, version=0x29, raw=None):
    keys = []
    def kv(doc):
        payload = b""
        for key, value in doc.items():
            if key not in keys:
                keys.append(key)
            name = struct.pack("<I", keys.index(key)) if version == 0x29 else key.encode() + b"\0"
            if isinstance(value, dict):
                payload += b"\0" + name + kv(value)
            elif isinstance(value, int):
                payload += b"\2" + name + struct.pack("<i", value)
            else:
                payload += b"\1" + name + value.encode() + b"\0"
        return payload + b"\10"
    records = b""
    for ident, doc in apps.items():
        data = kv({"appinfo": doc}) if raw is None or ident not in raw else raw[ident]
        records += struct.pack("<II", int(ident), len(data) + 60) + bytes(60) + data
    records += bytes(4)
    header = struct.pack("<II", 0x07564400 + version, 1)
    if version == 0x29:
        header += struct.pack("<Q", 16 + len(records))
        records += struct.pack("<I", len(keys)) + b"".join(k.encode() + b"\0" for k in keys)
    path = paths.steam / "appcache/appinfo.vdf"
    atomic(path, header + records)
    return path


@pytest.mark.parametrize("version", [0x28, 0x29])
def test_appinfo_formats_extract_configuration_and_valve_overrides(paths, version):
    appinfo = importlib.import_module("deckcompanion.appinfo")
    path = appinfo_file(paths, {"42": profile(), "891390": {"extended": {
        "app_mappings": {"43": {"tool": "proton_10"}},
        "compat_tools": {"proton_10": {"appid": "1493710", "from_oslist": "windows", "to_oslist": "linux"}}}}}, version)
    data = appinfo.parse(path)
    assert data["apps"]["42"]["configuration"] == {"recommended_runtime": "proton-stable", "requires_h264": "1"}
    assert appinfo.recommendation(data, "42") == "proton-stable"
    assert appinfo.recommendation(data, "43") == "proton_10"
    assert data["compat_tools"]["proton_10"]["appid"] == "1493710"


@pytest.mark.parametrize("version", [0x28, 0x29])
def test_unknown_value_skips_only_its_record(paths, version):
    appinfo = importlib.import_module("deckcompanion.appinfo")
    path = appinfo_file(paths, {"41": {}, "42": profile()}, version, raw={"41": b"\x7fgarbage"})
    logs = []
    data = appinfo.parse(path, logs.append)
    assert "41" not in data["apps"] and appinfo.recommendation(data, "42") == "proton-stable"
    assert "41 skipped" in logs[0]


@pytest.mark.parametrize("damage", [b"", bytes(16), struct.pack("<IIQ", 0x07564429, 1, 99999)])
def test_corrupt_appinfo_fails_open_without_writing_steam(paths, damage):
    atomic(paths.steam / "appcache/appinfo.vdf", damage)
    assert invoke(paths, "game", "42")["valve_recommendation"] is None
    assert not steam.mapping_path(paths).exists()


def test_appinfo_cache_invalidates_by_mtime_and_survives_unwritable_cache(paths, monkeypatch):
    appinfo = importlib.import_module("deckcompanion.appinfo")
    path = appinfo_file(paths, {"42": profile()})
    assert appinfo.load(paths)["apps"]["42"]["recommended_runtime"] == "proton-stable"
    appinfo._CACHE.clear()
    original = appinfo.parse
    monkeypatch.setattr(appinfo, "parse", lambda *a: pytest.fail("unchanged file must use disk cache"))
    assert appinfo.load(paths)["apps"]["42"]["recommended_runtime"] == "proton-stable"
    monkeypatch.setattr(appinfo, "parse", original)
    old = path.stat().st_mtime_ns
    appinfo_file(paths, {"42": profile("proton-experimental")})
    os.utime(path, ns=(old + 1_000_000, old + 1_000_000))
    monkeypatch.setattr(appinfo, "write_json", lambda *a: (_ for _ in ()).throw(OSError("read only")))
    assert appinfo.load(paths)["apps"]["42"]["recommended_runtime"] == "proton-experimental"


def manifest(paths, ident, library=None, **fields):
    library = library or paths.steam
    entry = {"appid": ident, "name": "Game " + ident, "installdir": "game-" + ident, **fields}
    atomic(library / f"steamapps/appmanifest_{ident}.acf", vdf.dumps({"AppState": entry}))


def mappings(paths):
    return steam.mappings(steam.read_vdf(steam.mapping_path(paths)))


@pytest.fixture
def library(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    appinfo_file(paths, {"42": profile(), "43": profile("native", "linux"),
        "44": profile("native", "windows,linux", depots={"144": {"config": {"oslist": "linux"}}}),
        "45": profile("proton-stable", "windows,linux", depots={"145": {"config": {"oslist": "windows"}}}),
        "46": {"common": {"oslist": "macos"}}, "47": {"common": {"type": "tool", "oslist": "windows"}},
        "50": profile("native", "windows,linux", depots={"150": {"config": {"oslist": "linux"}}})})
    for ident in ("42", "43", "44", "45", "46", "47", "48", "50"):
        manifest(paths, ident, InstalledDepots={"1" + ident: {"manifest": "1"}})
    # A secondary library, with explicit Windows override in its manifest.
    other = paths.data / "sdcard"
    atomic(paths.steam / "steamapps/libraryfolders.vdf", vdf.dumps({"libraryfolders": {"1": {"path": str(other)}}}))
    manifest(paths, "49", library=other, UserConfig={"platform_override_dest": "windows"})
    # Quoted Windows .EXE, a native script, and the management shortcut.
    atomic(steam.shortcut_path(paths), vdf.binary_dumps({"shortcuts": {
        "0": {"appid": -42, "AppName": "Windows shortcut", "Exe": '"/games/HELLO.EXE"', "tags": {"0": "Keep"}},
        "1": {"appid": -43, "AppName": "Native shortcut", "Exe": '"/games/native.sh"'},
        "2": {"appid": -44, "AppName": "DeckCompanion", "Exe": '"/bin/deckcompanion-ui"'}}}))
    doc = {"unrelated": "keep"}
    steam.mappings(doc).update({"42": {"name": "Proton-GE Latest", "priority": "7", "config": "custom", "extra": {"key": "value"}},
                               str(2**32 - 42): {"name": "proton-stable", "priority": "1"}, "99": {"name": "foreign"}})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    return doc


def test_default_takeover_queues_and_round_trips_exact_mappings(paths, library):
    assert invoke(paths, "settings")["takeover_all"] is True
    before = steam.mapping_path(paths).read_bytes()
    result = invoke(paths, "settings", "--set", "takeover_all=on")
    assert result["takeover_all"] is True
    assert steam.mapping_path(paths).read_bytes() == before
    expected = {"42", "45", "49", str(2**32 - 42)}
    assert {i for i, g in state(paths)["games"].items() if g.get("pending")} == expected
    shortcut_before = steam.shortcut_path(paths).read_bytes()
    steam.apply(paths)
    assert {i for i, m in mappings(paths).items() if m["name"] == "DeckCompanion"} == expected
    previous = state(paths)["games"]["42"]["previous_mapping"]
    assert previous == steam.mappings(library)["42"]
    steam.apply(paths)
    assert state(paths)["games"]["42"]["previous_mapping"] == previous
    info = invoke(paths, "game", "42")
    assert info["takeover"] == "on" and info["previous_mapping"] == "Proton-GE Latest"
    rows = {g["appid"]: g for g in invoke(paths, "games")["games"]}
    assert rows[str(2**32 - 42)]["takeover"] == "on"
    assert rows["43"]["takeover"] == rows["48"]["takeover"] == "n/a"
    status = invoke(paths, "status")
    assert status["takeover_all"] and status["takeover_counts"] == {"eligible": 4, "on": 4, "excluded": 0, "applied": 4, "pending": 0}
    invoke(paths, "settings", "--set", "takeover_all=off")
    assert invoke(paths, "status")["takeover_counts"]["pending"] == 4
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == library
    assert steam.shortcut_path(paths).read_bytes() == shortcut_before


def test_exclusion_restores_and_reinclude_captures_new_mapping(paths, library):
    steam.apply(paths)
    result = invoke(paths, "game-option", "42", "--takeover", "off")
    assert result["takeover"] == "excluded" and result["pending"] == "disable"
    steam.apply(paths)
    assert mappings(paths)["42"] == steam.mappings(library)["42"]
    steam.apply(paths)
    assert mappings(paths)["42"] == steam.mappings(library)["42"]
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)["42"] = {"name": "proton_10", "config": "new settings"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    invoke(paths, "game-option", "42", "--takeover", "on")
    steam.apply(paths)
    invoke(paths, "settings", "--set", "takeover_all=off")
    steam.apply(paths)
    assert mappings(paths)["42"] == {"name": "proton_10", "config": "new settings"}


def test_disable_is_persistent_exclusion_and_missing_game_restores(paths, library):
    steam.apply(paths)
    invoke(paths, "disable", "42")
    steam.apply(paths)
    assert invoke(paths, "game", "42")["takeover"] == "excluded"
    (paths.steam / "steamapps/appmanifest_45.acf").unlink()
    steam.apply(paths)
    assert "45" not in mappings(paths)
    assert mappings(paths)["42"] == steam.mappings(library)["42"]


def test_cli_apply_discovers_new_games_even_when_queue_empty(paths, library, monkeypatch, capsys):
    steam.apply(paths)
    assert not steam.has_pending(state(paths))
    manifest(paths, "51", oslist="windows")
    assert cli.main(["apply", "--json"]) == 0
    capsys.readouterr()
    assert mappings(paths).get("51", {}).get("name") == "DeckCompanion"


def test_uninstall_restores_takeover_without_reenabling(paths, library):
    steam.apply(paths)
    assert mappings(paths)["42"]["name"] == "DeckCompanion"
    install.uninstall(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == library
    assert not any(g.get("enabled") for g in state(paths)["games"].values())
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == library


def test_install_and_update_queue_new_games_without_steam_writes(paths, library, tmp_path, monkeypatch):
    # Minimal extracted installer source, all payload owned by this fixture.
    root = tmp_path / "release"
    for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py"):
        atomic(root / name, "fake")
    repo = Path(__file__).resolve().parents[1]
    for name in ("compat/DeckCompanion", "compatibilitytool.vdf", "toolmanifest.vdf"):
        atomic(root / name, (repo / name).read_bytes())
    if hasattr(install.os, "geteuid"):
        monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    before = steam.mapping_path(paths).read_bytes()
    install.install(paths, root)
    assert state(paths)["games"]["42"].get("takeover_managed") is True
    assert steam.mapping_path(paths).read_bytes() == before
    steam.apply(paths)
    manifest(paths, "51", oslist="windows")
    from deckcompanion import proton, umu
    from deckcompanion.common import Error
    monkeypatch.setattr(update, "json_url", lambda *a: (_ for _ in ()).throw(Error("offline", "fixture")))
    monkeypatch.setattr(proton, "sync", lambda *a: {})
    monkeypatch.setattr(umu, "install", lambda *a: {})
    before = steam.mapping_path(paths).read_bytes()
    assert update.update(paths)["steam_changes_pending"] is True
    assert state(paths)["games"]["51"]["pending"] == "enable"
    assert steam.mapping_path(paths).read_bytes() == before


def test_takeover_uses_detached_apply_retry_and_restores_original(paths, library, monkeypatch, capsys):
    # Reuse the existing fake clock/process-table harness; never start a helper or Steam.
    from test_apply_respawn import Processes, process_table
    from deckcompanion import apply_helper as helper
    invoke(paths, "settings", "--set", "takeover_all=on")
    table = Processes([.01, .2])
    process_table(monkeypatch, table)
    receipt = helper.apply_helper(paths, True)
    assert receipt["verified"] and receipt["attempts"] == 2 and receipt["errors"] == []
    assert state(paths)["games"]["42"]["previous_mapping"] == steam.mappings(library)["42"]
    assert not steam.has_pending(state(paths))
    # CLI schedules through the existing detached helper while Steam is running.
    manifest(paths, "51", oslist="windows")
    scheduled = []
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(steam, "running", lambda: True)
    monkeypatch.setattr(helper, "schedule", lambda p: scheduled.append(state(p)) or {"scheduled": True})
    assert cli.main(["apply", "--restart-steam", "--json"]) == 0
    capsys.readouterr()
    assert scheduled[0]["games"]["51"]["pending"] == "enable"


def test_parser_bounds_a_bad_record_and_continues_after_bad_table_index(paths):
    appinfo = importlib.import_module("deckcompanion.appinfo")
    bad = b"\1" + struct.pack("<I", 999999) + b"ignore\0\10"
    path = appinfo_file(paths, {"41": {}, "42": profile()}, raw={"41": bad})
    assert set(appinfo.parse(path)["apps"]) == {"42"}
    # A truncated payload must not read through into the next app's header.
    path = appinfo_file(paths, {"41": {}, "42": profile()}, version=0x28,
                        raw={"41": b"\1unterminated"})
    assert set(appinfo.parse(path)["apps"]) == {"42"}


def test_invalid_optional_valve_fields_do_not_break_runtime_selection(paths):
    runtimes(paths)
    appinfo_file(paths, {"42": profile(123), "891390": {"extended": {
        "app_mappings": "bad type", "compat_tools": "bad type"}}})
    result = invoke(paths, "game", "42")
    assert result["valve_recommendation"] is None
    assert result["proton"] == {"name": "GE-Proton11-11", "reason": "newest"}


def test_exclude_preexisting_manually_enabled_game_restores_mapping(paths, library):
    previous = {"name": "proton_10", "config": "legacy"}
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)["42"] = {"name": "DeckCompanion", "config": "", "priority": "250"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    save_state(paths, {"games": {"42": {"enabled": True, "previous_mapping": previous}}})
    result = invoke(paths, "game-option", "42", "--takeover", "off")
    assert result["takeover"] == "excluded" and result["pending"] == "disable"
    steam.apply(paths)
    assert mappings(paths)["42"] == previous


def test_apply_failure_keeps_snapshot_and_disk_mapping(paths, library, monkeypatch):
    invoke(paths, "settings", "--set", "takeover_all=on")
    before = steam.mapping_path(paths).read_bytes()
    monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        steam.apply(paths)
    assert steam.mapping_path(paths).read_bytes() == before
    assert state(paths)["games"]["42"]["previous_mapping"] == steam.mappings(library)["42"]


def runtimes(paths):
    for name in ("GE-Proton11-11", "Proton - Experimental", "Proton 11.0", "Proton 10.0"):
        atomic(paths.tools / name / "proton", "fake")


@pytest.mark.parametrize("recommendation,expected,reason", [
    ("proton-stable", "Proton 11.0", "valve_recommended"),
    ("proton-experimental", "Proton - Experimental", "valve_recommended"),
    ("proton_10", "Proton 10.0", "valve_recommended"),
    ("proton_11.0", "Proton 11.0", "valve_recommended"),
    ("Proton 10.0", "Proton 10.0", "valve_recommended"),
    ("proton_8", "GE-Proton11-11", "newest"),
    ("native", "GE-Proton11-11", "newest")])
def test_valve_runtime_preview_and_launch_agree(paths, monkeypatch, recommendation, expected, reason):
    runtimes(paths)
    appinfo_file(paths, {"42": profile(recommendation)})
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": expected, "reason": reason}
    assert result["valve_recommendation"] == recommendation
    info = {}
    args, env = launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"}, info)
    assert Path(args[0]).parent.name == expected and info["proton"] == result["proton"]
    log = (paths.data / "logs/42-last.log").read_text()
    assert reason in log
    if reason == "newest":
        assert f"Valve recommendation unavailable: {recommendation}" in log


def test_recipe_user_valve_newest_precedence_and_preserved_user_mapping(paths, monkeypatch):
    runtimes(paths)
    appinfo_file(paths, {"42": profile()})
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    assert invoke(paths, "game", "42")["proton"]["reason"] == "valve_recommended"
    invoke(paths, "game-option", "42", "--proton", "Proton 10.0")
    assert invoke(paths, "game", "42")["proton"] == {"name": "Proton 10.0", "reason": "user_override"}
    monkeypatch.setattr(recipes, "load", lambda *a: {"runtime": {"proton": "GE-Proton11-11"}, "version": "1"})
    assert invoke(paths, "game", "42")["proton"] == {"name": "Proton 10.0", "reason": "user_override"}
    assert invoke(paths, "game-option", "42", "--proton", "auto")["proton"]["reason"] == "pinned"
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    invoke(paths, "game-option", "42", "--proton", "auto")
    doc = {}
    steam.mappings(doc)["42"] = {"name": "proton_10", "config": "keep"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    manifest(paths, "42", oslist="windows")
    steam.apply(paths)
    assert invoke(paths, "game", "42")["proton"] == {"name": "Proton 10.0", "reason": "user_override"}
    # A missing user override must not silently select Valve's different version.
    result = invoke(paths, "game-option", "42", "--proton", "Proton 8.0")
    assert result["proton"] == {"name": "GE-Proton11-11", "reason": "newest"}


def test_steamplay_mapping_and_tool_appid_resolve_installed_build(paths, monkeypatch):
    runtimes(paths)
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    appinfo_file(paths, {"891390": {"extended": {"app_mappings": {"42": {"tool": "proton_special"}},
        "compat_tools": {"proton_special": {"appid": "123"}}}}})
    manifest(paths, "123")
    atomic(paths.steam / "steamapps/common/game-123/proton", "fake")
    # Discovery accepts Proton-named build directories; use a real Valve-style folder.
    doc = steam.read_vdf(paths.steam / "steamapps/appmanifest_123.acf")
    doc["AppState"]["installdir"] = "Proton 10.0"
    atomic(paths.steam / "steamapps/appmanifest_123.acf", vdf.dumps(doc))
    result = invoke(paths, "game", "42")
    assert result["valve_recommendation"] == "proton_special"
    assert result["proton"] == {"name": "Proton 10.0", "reason": "valve_recommended"}
