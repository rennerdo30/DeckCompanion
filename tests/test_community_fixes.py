"""Offline community selection, content channel and JSON regressions."""
import importlib.util
import os
from pathlib import Path

import pytest

from deckcompanion import channels, cli, launch, nonsteam, recipes, steam
from deckcompanion.common import Error, atomic, read_json, save_state, write_json


def invoke(paths, *args):
    return cli.execute(cli.parser().parse_args([*args, "--json"]), paths)


def build(paths, name, *ids):
    root = paths.tools / name
    atomic(root / "proton", "fake")
    for ident in ids:
        kind = "umu" if ident.startswith("umu-") else "steam"
        # Discovery must never execute fix code.
        atomic(root / "protonfixes" / ("gamefixes-" + kind) / (ident + ".py"), "raise RuntimeError('do not import')")
    return root


@pytest.fixture
def setup_game(paths, monkeypatch):
    from test_takeover_valve import appinfo_file, profile
    appinfo_file(paths, {"42": profile("proton-stable")})
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    build(paths, "Proton 11.0")
    return paths


@pytest.mark.parametrize("name", ["GE-Proton10-9", "UMU-Proton10-2", "Proton-9.0-GE-1"])
def test_community_fix_beats_newer_valve_and_matches_splash(setup_game, name):
    paths = setup_game
    fixed = build(paths, name, "42")
    build(paths, "GE-Proton11-11", "99")  # Newest GE does not contain this fix.
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": name, "reason": "community_fix"}
    assert result["community_fix"] == "applied" and result["community_fix_source"] == name
    assert result["valve_recommendation"] == "proton-stable"
    info = {}
    args, _ = launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"}, info)
    assert Path(args[0]) == fixed / "proton"
    assert info["proton"] == result["proton"]
    assert (info["community_fix"], info["community_fix_source"]) == ("applied", name)


def test_newest_build_containing_fix_is_selected(setup_game):
    paths = setup_game
    for name in ("GE-Proton10-1", "GE-Proton10-9", "UMU-Proton10-4"):
        build(paths, name, "42")
    build(paths, "GE-Proton11-11", "99")
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": "GE-Proton10-9", "reason": "community_fix"}
    assert result["community_fix_source"] == "GE-Proton10-9"


@pytest.mark.parametrize("winner,fixed", [("Proton 11.0", False), ("GE-Proton10-9", True)])
def test_explicit_override_wins_and_reports_actual_fix(setup_game, winner, fixed):
    paths = setup_game
    build(paths, "GE-Proton10-9", "42")
    result = invoke(paths, "game-option", "42", "--proton", winner)
    assert result["proton"] == {"name": winner, "reason": "user_override"}
    assert result["community_fix"] == ("applied" if fixed else None)
    assert result["community_fix_source"] == (winner if fixed else None)


def test_override_wins_recipe_pin_and_auto_restores_pin(setup_game, monkeypatch):
    paths = setup_game
    build(paths, "GE-Proton10-9", "42")
    invoke(paths, "game-option", "42", "--proton", "GE-Proton10-9")
    monkeypatch.setattr(recipes, "load", lambda *a: {"version": "1", "runtime": {"proton": "Proton 11.0"}})
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": "GE-Proton10-9", "reason": "user_override"}
    assert result["community_fix"] == "applied"
    result = invoke(paths, "game-option", "42", "--proton", "auto")
    assert result["proton"] == {"name": "Proton 11.0", "reason": "pinned"}
    assert result["community_fix"] is None and result["community_fix_source"] is None


@pytest.mark.parametrize("name", ["GE-Proton10-9", "UMU-Proton10-2", "Proton-9.0-GE-1"])
def test_missing_recipe_pin_stays_in_community_family(setup_game, monkeypatch, name):
    paths = setup_game
    build(paths, name)
    monkeypatch.setattr(recipes, "load", lambda *a: {"version": "1", "runtime": {"proton": "GE-Proton99-1"}})
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": name, "reason": "recipe_fallback"}
    assert result["community_fix"] is None
    build(paths, name, "42")
    result = invoke(paths, "game", "42")
    assert result["proton"] == {"name": name, "reason": "community_fix"}
    assert result["community_fix"] == "applied"


def test_saved_steam_override_is_preserved(setup_game):
    paths = setup_game
    build(paths, "GE-Proton10-9", "42")
    save_state(paths, {"games": {"42": {"enabled": True, "previous_mapping": {"name": "proton-stable"}}}})
    result = invoke(paths, "game", "42")
    assert result["proton"]["reason"] == "user_override"
    assert result["community_fix"] is None


@pytest.mark.parametrize("known", [False, True])
def test_default_and_helpers_are_not_game_fixes(setup_game, known):
    paths = setup_game
    root = build(paths, "GE-Proton10-9", "default", "helper", "99")
    (root / "protonfixes/gamefixes-steam/42.py").mkdir()
    if known:
        write_json(paths.cache / "index.json", {"community_fixes": {"steam": ["42"], "umu": []}})
    # Valve's own directories do not make Valve Proton a protonfixes runner.
    build(paths, "Proton 11.0", "42")
    result = invoke(paths, "game", "42")
    assert result["proton"]["reason"] == "valve_recommended"
    assert result["community_fix"] == ("available_not_installed" if known else None)
    assert result["community_fix_source"] is None


def test_known_content_fix_retains_valve_until_matching_build_installed(setup_game):
    paths = setup_game
    write_json(paths.cache / "index.json", {"community_fixes": {"steam": ["42"], "umu": []}})
    build(paths, "GE-Proton11-11", "99")
    before = invoke(paths, "game", "42")
    assert before["community_fix"] == "available_not_installed"
    assert before["community_fix_source"] is None
    assert before["proton"]["reason"] == "valve_recommended"
    info = {}
    launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"}, info)
    assert info["community_fix"] == "available_not_installed"
    build(paths, "GE-Proton10-9", "42")
    assert invoke(paths, "game", "42")["proton"]["reason"] == "community_fix"


def test_preference_default_round_trip_and_no_downloads(setup_game):
    paths = setup_game
    build(paths, "GE-Proton10-9", "42")
    assert invoke(paths, "settings")["prefer_community_fixes"] is True
    assert invoke(paths, "settings", "--set", "prefer_community_fixes=off")["prefer_community_fixes"] is False
    result = invoke(paths, "game", "42")
    assert result["proton"]["reason"] == "valve_recommended" and result["community_fix"] is None
    invoke(paths, "settings", "--set", "prefer_community_fixes=on")
    assert invoke(paths, "game", "42")["community_fix"] == "applied"


def test_cache_reuses_scan_and_invalidates_added_removed_fix_and_build_path(setup_game, monkeypatch):
    paths = setup_game
    root = build(paths, "GE-Proton10-9", "42")
    assert invoke(paths, "game", "42")["community_fix"] == "applied"
    from deckcompanion import community
    original = community.scan
    monkeypatch.setattr(community, "scan", lambda *a: pytest.fail("unchanged build should use cache"))
    assert invoke(paths, "game", "42")["community_fix"] == "applied"
    monkeypatch.setattr(community, "scan", original)
    directory = root / "protonfixes/gamefixes-steam"
    stamp = directory.stat().st_mtime_ns
    (directory / "42.py").unlink()
    os.utime(directory, ns=(stamp + 10000000, stamp + 10000000))
    assert invoke(paths, "game", "42")["community_fix"] is None
    atomic(directory / "42.py", "# restored")
    os.utime(directory, ns=(stamp + 20000000, stamp + 20000000))
    assert invoke(paths, "game", "42")["community_fix"] == "applied"
    # Different paths with identical build names must not share IDs.
    other = paths.steam / "steamapps/common/GE-Proton10-9"
    atomic(other / "proton", "fake")
    available = [{"name": root.name, "path": str(other)}, {"name": "Proton 11.0", "path": str(paths.tools / "Proton 11.0")}]
    assert cli.game(paths, "42", available=available)["community_fix"] is None


@pytest.mark.parametrize("broken", ["corrupt", "missing-kind", "readonly"])
def test_bad_or_unwritable_cache_does_not_hide_fix(setup_game, monkeypatch, broken):
    paths = setup_game
    build(paths, "GE-Proton10-9", "42")
    assert invoke(paths, "game", "42")["community_fix"] == "applied"
    if broken in ("corrupt", "missing-kind"):
        for cache in (paths.cache / "community-fixes").glob("*.json"):
            if broken == "corrupt":
                atomic(cache, "bad json")
            else:
                data = read_json(cache)
                data["ids"].pop("steam")
                write_json(cache, data)
    else:
        from deckcompanion import community
        build(paths, "GE-Proton11-11", "42")  # An uncached, newer matching build.
        monkeypatch.setattr(community, "write_json", lambda *a: (_ for _ in ()).throw(OSError("read only")))
    assert invoke(paths, "game", "42")["proton"]["reason"] == "community_fix"


@pytest.mark.parametrize("bad", [[], {"steam": ["default"]}, {"steam": ["42", "42"]},
                                  {"umu": ["../42"]}, {"umu": ["umu-default"]}, {"steam": [42]}])
def test_invalid_content_fix_ids_rejected(bad):
    candidate = {"schema_version": 1, "content_version": "2026.11.1", "min_engine": "0.4.3",
                 "recipes": {}, "community_fixes": bad}
    with pytest.raises(Error, match="community|fix"):
        channels.validate(candidate)


def test_content_builder_emits_independent_index_and_engine_merge_keeps_it(tmp_path, monkeypatch):
    root = tmp_path / "content-root"
    for kind, ident in (("steam", "42"), ("umu", "umu-42")):
        directory = root / "third_party/umu-protonfixes" / ("gamefixes-" + kind)
        atomic(directory / (ident + ".py"), "# fix")
        atomic(directory / "default.py", "# default")
    script = Path(__file__).resolve().parents[1] / "scripts/build_content.py"
    spec = importlib.util.spec_from_file_location("community_content_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "ROOT", root)
    builder.build(tmp_path / "output", "2026.11.1", offline=True)
    index = read_json(tmp_path / "output/content-index.json")
    assert index["community_fixes"] == {"steam": ["42"], "umu": ["umu-42"]}
    merged = recipes.merge_index(index, {"engine": {"version": "999"}, "recipes": {}})
    assert merged["community_fixes"] == index["community_fixes"]


def test_umu_id_uses_umu_fixes_and_json_not_steam_appid(setup_game, tmp_path, monkeypatch):
    paths = setup_game
    build(paths, "GE-Proton11-11", "42")
    fixed = build(paths, "UMU-Proton10-2", "umu-42")
    exe = tmp_path / "Game.exe"
    atomic(exe, "fake")
    result = nonsteam.add(paths, str(exe), "Example", umu_id="umu-42")
    assert result["community_fix"] == "applied" and result["community_fix_source"] == fixed.name
    assert result["proton"]["reason"] == "community_fix"
    saved = nonsteam.entry(paths, result["slug"])
    selected, reason = nonsteam.selection(paths, saved)
    assert selected["name"] == fixed.name and reason == "community_fix"
    monkeypatch.setattr("deckcompanion.umu.install", lambda *a: {"path": "/fake/umu-run"})
    _, env, reason = nonsteam.runtime(paths, saved, None)
    assert Path(env["PROTONPATH"]) == fixed and env["GAMEID"] == "umu-42"


def test_games_list_includes_community_fields(setup_game, monkeypatch):
    paths = setup_game
    from test_takeover_valve import manifest
    manifest(paths, "42", oslist="windows")
    build(paths, "GE-Proton10-9", "42")
    result = invoke(paths, "games")
    row = next(g for g in result["games"] if g["appid"] == "42")
    assert row["community_fix"] == "applied" and row["community_fix_source"] == "GE-Proton10-9"
