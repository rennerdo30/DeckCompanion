"""Hardware-report regressions using synthetic appinfo/Steam data only."""
from copy import deepcopy
from datetime import datetime
import pytest
import vdf
from deckcompanion import install, launch, recipes, steam
from deckcompanion.common import atomic, save_state, state
from test_takeover_valve import appinfo_file, invoke, manifest, mappings, profile


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(steam.subprocess, "Popen", lambda *a, **k: pytest.fail("No real Steam/process"))


@pytest.mark.parametrize("ident,runtime,oslist,mapping", [
    ("4634560", "proton-experimental", "windows,linux", "Proton-GE Latest"),
    ("3558600", "proton-11.0-beta", "windows,linux", None),
    ("3564740", "proton-11.0-beta", "linux", None),
    ("4162040", None, "windows,linux", "proton-stable"),
    ("2694490", None, "", "custom-compat-tool"),
])
def test_windows_evidence_beats_native_depots(paths, ident, runtime, oslist, mapping):
    doc = {}
    if mapping:
        steam.mappings(doc)[ident] = {"name": mapping, "config": "custom", "extra": {"keep": "yes"}}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    appinfo_file(paths, {ident: profile(runtime or "", oslist, depots={"1": {"config": {"oslist": "linux"}}})})
    manifest(paths, ident, InstalledDepots={"1": {"manifest": "1"}})
    invoke(paths, "settings", "--set", "takeover_all=on")
    result = invoke(paths, "game", ident)
    assert result["takeover"] == "on"
    assert state(paths)["takeover_snapshot"]["entries"][ident] == steam.mappings(doc).get(ident)
    assert result["previous_mapping"] == mapping


@pytest.mark.parametrize("kind,name,ident", [
    ("Tool", "A custom tool", "1001"), ("Config", "Configuration", "1002"),
    ("Driver", "A driver", "1003"), ("", "Steamworks Common Redistributables", "228980"),
    ("", "Steam Linux Runtime 4.0", "4183110"), ("", "Proton 11.0", "4628710"),
    ("", "Proton Hotfix", "2180100"), ("", "Proton Experimental", "1493710"),
    ("", "Steam Linux Runtime 1.0", "1070560"), ("", "Steam Linux Runtime 2.0", "1391110"),
    ("", "Steam Linux Runtime 3.0", "1628350"), ("", "Steamworks SDK Redist", "1007"),
])
def test_non_game_apps_are_hidden_and_never_taken_over(paths, kind, name, ident):
    app = profile("proton-stable", "windows")
    app["common"]["type"] = kind
    appinfo_file(paths, {ident: app})
    manifest(paths, ident, name=name, oslist="windows")
    save_state(paths, {"games": {ident: {"mods": {}, "pending": "enable"}}})
    assert ident not in {g["appid"] for g in invoke(paths, "games")["games"]}
    steam.apply(paths)
    assert ident not in mappings(paths)


def test_hidden_legacy_tool_is_restored_instead_of_reenabled(paths):
    ident = "228980"
    manifest(paths, ident, name="Steamworks Common Redistributables", oslist="windows")
    previous = {"name": "foreign", "config": "preserve"}
    doc = {}
    steam.mappings(doc)[ident] = {"name": "DeckCompanion", "config": "", "priority": "250"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    save_state(paths, {"games": {ident: {"enabled": True, "previous_mapping": previous,
        "takeover_managed": True, "takeover_snapshot": True}}})
    assert ident not in {g["appid"] for g in invoke(paths, "games")["games"]}
    steam.apply(paths)
    assert mappings(paths)[ident] == previous


@pytest.mark.parametrize("runtime,installed,expected", [
    ("proton-11.0-beta", ["Proton 11.0"], "Proton 11.0"),
    ("proton-9.0-3RC", ["Proton 9.0"], "Proton 9.0"),
    ("proton-10.0-3", ["Proton 10.0"], "Proton 10.0"),
    ("proton-11.1-beta", ["Proton 11.2", "Proton 11.3", "Proton 12.0"], "Proton 11.2"),
    ("proton-10.0-beta", ["Proton 11.0", "Proton - Experimental"], "Proton - Experimental"),
    ("proton-9.0-3RC", ["Proton 9.0", "Proton 11.0"], "Proton 9.0"),
    ("proton-11.3-beta", ["Proton 11.0", "Proton - Experimental"], "Proton - Experimental"),
])
def test_valve_version_suffixes_and_mapping_are_logged(paths, monkeypatch, runtime, installed, expected):
    for name in ["GE-Proton11-11", *installed]:
        atomic(paths.tools / name / "proton", "fake")
    appinfo_file(paths, {"42": profile(runtime)})
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    wanted_reason = "valve_recommended"
    assert invoke(paths, "game", "42")["proton"] == {"name": expected, "reason": wanted_reason}
    logs = []
    selected, reason, _ = launch.selection(paths, "42", {}, log=logs.append)
    assert selected["name"] == expected and reason == wanted_reason
    assert any(runtime in line and expected in line for line in logs)
    available = steam.proton(paths)
    pinned, pinned_reason = launch.select_proton(available, {"runtime": {"proton": "GE-Proton11-11"}},
                                               override=expected, valve=runtime)
    assert pinned["name"] == expected and pinned_reason == "user_override"
    chosen, chosen_reason = launch.select_proton(available, {}, override="GE-Proton11-11", valve=runtime)
    assert chosen["name"] == "GE-Proton11-11" and chosen_reason == "user_override"
    fallback, fallback_reason = launch.select_proton([p for p in available if p["name"].startswith("GE-")], {}, valve=runtime)
    assert fallback["name"] == "GE-Proton11-11" and fallback_reason == "newest"


@pytest.fixture
def restore_library(paths):
    apps = {"4634560": profile("proton-experimental", "windows,linux"),
            "3558600": profile("proton-11.0-beta", "windows,linux")}
    for ident in apps:
        manifest(paths, ident)
    appinfo_file(paths, apps)
    atomic(steam.shortcut_path(paths), vdf.binary_dumps({"shortcuts": {
        "0": {"appid": -1159606445, "AppName": "Shortcut", "Exe": '"/games/Game.exe"'},
        "1": {"appid": -2005784044, "AppName": "Shortcut two", "Exe": '"/games/Other.exe"'},
        "2": {"appid": -1622197939, "AppName": "Shortcut three", "Exe": '"/games/Third.exe"'}}}))
    doc = {"unrelated": "keep"}
    steam.mappings(doc).update({"0": {"name": "proton-stable", "priority": "20", "custom": {"x": "y"}},
        "4634560": {"name": "Proton-GE Latest", "priority": "7", "config": "custom", "extra": {"x": "y"}},
        "3135360851": {"name": "proton-stable", "config": "shortcut"},
        "2289183252": {"name": "Proton-GE Latest", "config": "shortcut two"},
        "2672769357": {"name": "proton-stable", "config": "shortcut three"},
        "99": {"name": "foreign"}})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    return deepcopy(doc)


def test_restore_all_exact_round_trip_and_idempotency(paths, restore_library):
    invoke(paths, "default", "--on")
    invoke(paths, "settings", "--set", "takeover_all=on")
    snapshot = deepcopy(state(paths)["takeover_snapshot"])
    datetime.fromisoformat(snapshot["taken_at"])
    assert snapshot["entries"] == {i: steam.mappings(restore_library).get(i)
                                    for i in ("0", "4634560", "3558600", "3135360851", "2289183252", "2672769357")}
    steam.apply(paths)
    invoke(paths, "game-option", "3558600", "--takeover", "off")
    before = steam.mapping_path(paths).read_bytes()
    shortcut = steam.shortcut_path(paths).read_bytes()
    result = invoke(paths, "restore-all")
    assert result == {"restoring": 6, "pending": [
        {"appid": "3558600", "action": "restore"}, {"appid": "4634560", "action": "restore"},
        {"appid": "2289183252", "action": "restore"}, {"appid": "2672769357", "action": "restore"},
        {"appid": "3135360851", "action": "restore"}, {"action": "restore_default"}]}
    assert invoke(paths, "restore-all") == result
    assert not state(paths)["settings"]["takeover_all"]
    assert not any(g.get("takeover_excluded") for g in state(paths)["games"].values())
    assert steam.mapping_path(paths).read_bytes() == before
    assert invoke(paths, "status")["takeover_snapshot"] == {"taken_at": snapshot["taken_at"], "games": 5}
    assert invoke(paths, "status")["pending"] == result["pending"]
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == restore_library
    assert steam.shortcut_path(paths).read_bytes() == shortcut
    assert invoke(paths, "restore-all") == {"restoring": 0, "pending": []}
    assert steam.apply(paths)["applied"] == []
    assert state(paths)["takeover_snapshot"] == snapshot


def test_snapshot_survives_reinclude_additions_and_restores_original(paths, restore_library):
    steam.apply(paths)
    original = deepcopy(state(paths)["takeover_snapshot"])
    invoke(paths, "game-option", "4634560", "--takeover", "off")
    steam.apply(paths)
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)["4634560"] = {"name": "new user setting"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    invoke(paths, "game-option", "4634560", "--takeover", "on")
    manifest(paths, "51", oslist="windows")
    steam.apply(paths)
    snapshot = state(paths)["takeover_snapshot"]
    assert snapshot["taken_at"] == original["taken_at"]
    assert snapshot["entries"] == {**original["entries"], "51": None}
    invoke(paths, "restore-all")
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == restore_library


def test_legacy_takeover_snapshot_uses_preserved_originals(paths):
    previous = {"name": "Proton-GE Latest", "config": "old", "extra": {"nested": "keep"}}
    default = {"name": "proton-stable", "priority": "30"}
    doc = {}
    steam.mappings(doc).update({i: {"name": "DeckCompanion", "config": "", "priority": "250"}
                              for i in ("0", "42", "43")})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    save_state(paths, {"games": {"42": {"enabled": True, "previous_mapping": previous, "takeover_snapshot": True},
                                 "43": {"enabled": True, "previous_mapping": None}},
                       "previous_default_mapping": default})
    result = invoke(paths, "restore-all")
    assert result["restoring"] == 3
    steam.apply(paths)
    assert mappings(paths) == {"0": default, "42": previous}


def test_restore_all_uninstall_uses_first_snapshot(paths, restore_library):
    steam.apply(paths)
    # Ordinary exclusion/reinclude updates the session snapshot, never the first snapshot.
    invoke(paths, "game-option", "4634560", "--takeover", "off")
    steam.apply(paths)
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)["4634560"] = {"name": "later choice"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    invoke(paths, "game-option", "4634560", "--takeover", "on")
    steam.apply(paths)
    install.uninstall(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == restore_library


def test_restore_queue_uses_detached_retry_and_preserves_snapshot(paths, restore_library, monkeypatch):
    from test_apply_respawn import Processes, process_table
    from deckcompanion import apply_helper
    invoke(paths, "default", "--on")
    steam.apply(paths)
    invoke(paths, "restore-all")
    snapshot = deepcopy(state(paths)["takeover_snapshot"])
    process_table(monkeypatch, Processes([.01, .2]))
    result = apply_helper.apply_helper(paths, True)
    assert result["verified"] and result["attempts"] == 2 and result["errors"] == []
    assert steam.read_vdf(steam.mapping_path(paths)) == restore_library
    assert not steam.has_pending(state(paths))
    assert state(paths)["takeover_snapshot"] == snapshot


def test_status_reports_no_snapshot_before_takeover(paths):
    assert invoke(paths, "status")["takeover_snapshot"] is None


def test_no_appinfo_mapping_is_evidence_and_native_multiplatform_is_skipped(paths):
    for ident in ("41", "42", "43", "44", "45", "46", "47"):
        manifest(paths, ident)
    appinfo_file(paths, {"43": profile("", "windows,linux"),
                         "44": profile("native", "windows,linux"),
                         "45": profile("", "macos"), "46": profile("", "linux"), "47": profile("", "macos")})
    doc = {}
    steam.mappings(doc).update({i: {"name": "Proton-GE Latest"} for i in ("41", "44", "47")})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    invoke(paths, "settings", "--set", "takeover_all=on")
    rows = {g["appid"]: g["takeover"] for g in invoke(paths, "games")["games"]}
    assert rows["41"] == "on"
    assert all(rows[i] == "n/a" for i in ("42", "43", "44", "45", "46", "47"))


@pytest.mark.parametrize("global_mapping", [None, {"name": "DeckCompanion", "config": "", "priority": "250"}])
def test_absent_or_preexisting_global_default_round_trip(paths, global_mapping):
    doc = {"keep": "yes"}
    if global_mapping:
        steam.mappings(doc)["0"] = global_mapping
    else:
        steam.mappings(doc)
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    manifest(paths, "42", oslist="windows")
    invoke(paths, "default", "--on")
    steam.apply(paths)
    assert state(paths)["takeover_snapshot"]["entries"]["0"] == global_mapping
    invoke(paths, "restore-all")
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == doc


def test_restore_verifies_complete_entries_and_rolls_back_on_state_failure(paths, restore_library, monkeypatch):
    steam.apply(paths)
    invoke(paths, "restore-all")
    original_snapshot = deepcopy(state(paths)["takeover_snapshot"])
    before = steam.mapping_path(paths).read_bytes()
    saved_queue = state(paths)["restore_pending"]
    real_save = steam.save_state
    monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        steam.apply(paths)
    assert steam.mapping_path(paths).read_bytes() == before
    assert state(paths)["restore_pending"] == saved_queue
    assert state(paths)["takeover_snapshot"] == original_snapshot
    monkeypatch.setattr(steam, "save_state", real_save)
    plan = steam.prepare_apply(paths)
    steam.commit_apply(paths, plan)
    assert steam.verify_apply(paths, plan)
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)["4634560"]["unexpected"] = "Steam overwrote the entry"
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    assert not steam.verify_apply(paths, plan)
