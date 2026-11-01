"""0.3.2 behavioral regressions. All Steam trees, processes and downloads are fake."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import vdf
from deckcompanion import __version__, cli, content, install, launch, localfix, net, recipes, steam, update
from deckcompanion.common import Error, atomic, read_json, save_state, state, write_json

APP = "2300320"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    if hasattr(steam, "game_running"):
        monkeypatch.setattr(steam, "game_running", lambda appid: False)
    def deny(*args, **kwargs):
        pytest.fail("No real processes in engine tests")
    monkeypatch.setattr(steam.subprocess, "Popen", deny)


def text(version="1.0.0", notes="New player notes", mod_version="v0.7.0", payload=b"deck mode"):
    sha = hashlib.sha256(payload).hexdigest()
    return f'''schema_version = 1
appid = "{APP}"
name = "Farming Simulator 25"
version = "{version}"
notes = "{notes}"
[runtime]
proton = "GE-Proton11-11"
[[mods]]
id = "fs25-deck-mode"
name = "FS25 Deck Mode"
version = "{mod_version}"
url = "https://example.org/mod.zip"
sha256 = "{sha}"
mode = "copy"
root = "prefix"
target = "pfx/mods/FS25_DeckMode.zip"
'''


def index(recipe, engine="0.2.0"):
    return {"schema_version": 1, "engine": {"version": engine, "url": "https://example.org/engine", "sha256": "a" * 64},
            "recipes": {APP: {"toml": recipe, "sha256": hashlib.sha256(recipe.encode()).hexdigest()}}}


@pytest.fixture
def game_paths(paths, tmp_path, monkeypatch):
    paths = replace(paths, recipes=tmp_path / "recipes")
    monkeypatch.setenv("DECKCOMPANION_RECIPES", str(paths.recipes))
    atomic(paths.recipes / f"{APP}.toml", text())
    atomic(paths.cache / "downloads" / hashlib.sha256(b"deck mode").hexdigest(), b"deck mode")
    atomic(paths.steam / f"steamapps/appmanifest_{APP}.acf", vdf.dumps({"AppState": {
        "appid": APP, "name": "Farming Simulator 25", "installdir": "FS25"}}))
    (paths.steam / f"steamapps/compatdata/{APP}/pfx").mkdir(parents=True)
    return paths


@pytest.fixture
def source(game_paths, tmp_path, monkeypatch):
    root = tmp_path / "release"
    for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "bin/deckcompanion-ui"):
        atomic(root / name, b"fake payload")
    for name in ("compat/DeckCompanion", "compatibilitytool.vdf", "toolmanifest.vdf"):
        atomic(root / name, (ROOT / name).read_bytes())
    atomic(root / f"recipes/{APP}.toml", text())
    atomic(root / "assets/steam/portrait.png", b"portrait")
    if hasattr(install.os, "geteuid"):
        monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    return root


def call(capsys, *args):
    code = cli.main([*args, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0, result
    return result


@pytest.mark.parametrize("cached_version,cached_engine", [("0.9.0", "99.0.0"), ("1.0.0", "0.2.0"), ("1.0.0", None)])
def test_old_cached_fs25_does_not_hide_bundled_notes(game_paths, cached_version, cached_engine):
    cached = index(text(cached_version, "Old developer notes"))
    if cached_engine:
        cached["engine"]["version"] = cached_engine
    else:
        cached.pop("engine")
    write_json(game_paths.cache / "index.json", cached)
    assert recipes.load(game_paths, APP)["notes"] == "New player notes"


# The second case: same recipe version, published by a newer engine than the running one.
@pytest.mark.parametrize("cached_version,cached_engine", [("1.0.1", "0.1.0"), ("1.0.0", "99.0.0")])
def test_newer_cached_fs25_is_retained(game_paths, cached_version, cached_engine):
    write_json(game_paths.cache / "index.json", index(text(cached_version, "Newer cached notes"), cached_engine))
    assert recipes.load(game_paths, APP)["notes"] == "Newer cached notes"


def test_recipe_versions_compare_numerically_across_all_sources(game_paths):
    atomic(game_paths.recipes / f"{APP}.toml", text("1.10.0"))
    atomic(game_paths.cache / f"recipes/{APP}.toml", text("1.2.0", "Stale standalone notes"))
    write_json(game_paths.cache / "index.json", index(text("1.9.0", "Stale indexed notes"), "99"))
    assert recipes.load(game_paths, APP)["version"] == "1.10.0"


def test_install_refreshes_old_index_with_per_recipe_provenance(game_paths, source):
    write_json(game_paths.cache / "index.json", index(text(notes="Old developer notes")))
    install.install(game_paths, source)
    cached = read_json(game_paths.cache / "index.json")
    assert "New player notes" in cached["recipes"][APP]["toml"]
    assert cached["recipes"][APP]["engine_version"] == __version__
    assert cached["engine"]["version"] == "0.2.0"  # Not a fabricated engine release.
    update.validate_index(cached)
    assert cli.game(game_paths, APP)["mods"][0]["state"] == "installed"


def test_install_invalidates_only_older_standalone_cache(game_paths, source):
    cached = game_paths.cache / f"recipes/{APP}.toml"
    atomic(cached, text("0.9.0", "Old developer notes"))
    install.install(game_paths, source)
    assert not cached.exists()
    assert recipes.load(game_paths, APP)["notes"] == "New player notes"
    assert not (game_paths.cache / "index.json").exists()


def test_install_release_index_keeps_a_newer_cached_recipe(game_paths, source):
    write_json(game_paths.cache / "index.json", index(text("2.0.0", "Future cached notes"), "0.3.99"))
    write_json(source / "index.json", index(text(), __version__))
    install.install(game_paths, source)
    assert recipes.load(game_paths, APP)["notes"] == "Future cached notes"
    cached = read_json(game_paths.cache / "index.json")
    assert cached["engine"]["version"] == __version__
    assert cached["recipes"][APP]["engine_version"] == "0.3.99"
    update.validate_index(cached)


@pytest.mark.parametrize("command", ["mod", "enable", "install"])
def test_immediate_install_adopts_identical_hand_installed_mod(game_paths, source, capsys, command):
    target = game_paths.steam / f"steamapps/compatdata/{APP}/pfx/mods/FS25_DeckMode.zip"
    atomic(target, b"deck mode")
    if command == "install":
        call(capsys, command, "--source", str(source))
        result = call(capsys, "game", APP)
    else:
        result = call(capsys, command, APP, *(["fs25-deck-mode", "--on"] if command == "mod" else []))
    assert result["mods"][0]["installed_version"] == "v0.7.0"
    assert result["mods"][0]["state"] == "installed"
    assert target.read_bytes() == b"deck mode"
    assert read_json(game_paths.data / f"manifests/{APP}.json")["mods"]["fs25-deck-mode"]["version"] == "v0.7.0"
    if command == "mod":
        assert result["mod_result"] == {"changed_now": True, "message": "Installed v0.7.0"}


def test_mod_install_off_and_reenable_are_immediate_and_idempotent(game_paths, capsys):
    target = game_paths.steam / f"steamapps/compatdata/{APP}/pfx/mods/FS25_DeckMode.zip"
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--on")["mod_result"]["changed_now"] is True
    assert target.read_bytes() == b"deck mode"
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--on")["mod_result"]["changed_now"] is False
    off = call(capsys, "mod", APP, "fs25-deck-mode", "--off")
    assert off["mod_result"] == {"changed_now": True, "message": "Off"}
    assert off["mods"][0]["state"] == "off" and not target.exists()
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--on")["mod_result"]["changed_now"] is True
    target.unlink()
    assert cli.game(game_paths, APP)["mods"][0]["state"] != "installed"
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--on")["mod_result"]["changed_now"] is True


@pytest.mark.parametrize("reason", ["running", "no_prefix", "unknown"])
def test_mod_deferred_without_writes_when_not_safe(game_paths, capsys, monkeypatch, reason):
    if reason == "no_prefix":
        (game_paths.steam / f"steamapps/compatdata/{APP}/pfx").rmdir()
    elif reason == "running":
        monkeypatch.setattr(steam, "game_running", lambda appid: True, raising=False)
    else:
        def unknown(appid):
            raise Error("game_state_unknown", "Cannot check all game processes safely")
        monkeypatch.setattr(steam, "game_running", unknown, raising=False)
    result = call(capsys, "mod", APP, "fs25-deck-mode", "--on")
    assert result["mod_result"]["changed_now"] is False
    assert result["mods"][0]["state"] == ("blocked" if reason == "unknown" else "will_install_on_launch")
    assert result["mods"][0]["state_message"]
    assert not (game_paths.data / f"manifests/{APP}.json").exists()


def test_modified_mod_is_blocked_preserved_and_reported(game_paths, capsys):
    call(capsys, "mod", APP, "fs25-deck-mode", "--on")
    target = game_paths.steam / f"steamapps/compatdata/{APP}/pfx/mods/FS25_DeckMode.zip"
    target.write_bytes(b"player edits")
    result = call(capsys, "mod", APP, "fs25-deck-mode", "--off")
    assert target.read_bytes() == b"player edits"
    assert result["mods"][0]["state"] == "blocked"
    assert result["mod_result"]["changed_now"] is False
    assert "keeping your files" in result["mod_result"]["message"]


def test_mod_off_waits_for_running_game_without_claiming_install(game_paths, capsys, monkeypatch):
    call(capsys, "mod", APP, "fs25-deck-mode", "--on")
    target = game_paths.steam / f"steamapps/compatdata/{APP}/pfx/mods/FS25_DeckMode.zip"
    monkeypatch.setattr(steam, "game_running", lambda appid: True, raising=False)
    result = call(capsys, "mod", APP, "fs25-deck-mode", "--off")
    assert result["mods"][0]["state"] == "off"
    assert result["mod_result"] == {"changed_now": False, "message": "Off; will remove on next launch"}
    assert target.read_bytes() == b"deck mode"
    monkeypatch.setattr(steam, "game_running", lambda appid: False, raising=False)
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--off")["mod_result"] == {"changed_now": True, "message": "Off"}
    (game_paths.steam / f"steamapps/compatdata/{APP}/pfx/mods").rmdir()
    (game_paths.steam / f"steamapps/compatdata/{APP}/pfx").rmdir()
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--off")["mod_result"] == {"changed_now": False, "message": "Off"}


def test_mod_update_available_while_running_then_installed_when_idle(game_paths, capsys, monkeypatch):
    call(capsys, "mod", APP, "fs25-deck-mode", "--on")
    atomic(game_paths.recipes / f"{APP}.toml", text(mod_version="v0.8.0", payload=b"new mod"))
    atomic(game_paths.cache / "downloads" / hashlib.sha256(b"new mod").hexdigest(), b"new mod")
    assert cli.game(game_paths, APP)["mods"][0]["state"] == "update_available"
    monkeypatch.setattr(steam, "game_running", lambda appid: True, raising=False)
    assert call(capsys, "mod", APP, "fs25-deck-mode", "--on")["mods"][0]["state"] == "update_available"
    monkeypatch.setattr(steam, "game_running", lambda appid: False, raising=False)
    result = call(capsys, "mod", APP, "fs25-deck-mode", "--on")
    assert result["mods"][0]["state"] == "installed"
    assert result["mods"][0]["installed_version"] == "v0.8.0"


def test_launch_safety_net_updates_mod_state(game_paths, monkeypatch):
    atomic(game_paths.tools / "GE-Proton11-11/proton", "fake")
    prefix = game_paths.steam / f"steamapps/compatdata/{APP}"
    launch.prepare(game_paths, "run", ["game.exe"], {"SteamAppId": APP})
    assert cli.game(game_paths, APP)["mods"][0]["state"] == "will_install_on_launch"
    launch.prepare(game_paths, "run", ["game.exe"], {"SteamAppId": APP, "STEAM_COMPAT_DATA_PATH": str(prefix)})
    assert cli.game(game_paths, APP)["mods"][0]["state"] == "installed"


@pytest.mark.parametrize("mode", ["engine", "shortcut", "art", "mapping"])
def test_install_queues_only_steam_visible_changes(game_paths, source, monkeypatch, mode, capsys):
    install.install(game_paths, source)
    steam.apply(game_paths)
    shortcut = steam.shortcut_path(game_paths)
    # Steam adds properties and the player changes tags; engine upgrades preserve both.
    doc = vdf.binary_loads(shortcut.read_bytes())
    doc["shortcuts"]["0"].update(LastPlayTime=123, tags={"0": "Favorites"})
    atomic(shortcut, vdf.binary_dumps(doc))
    before = {p: p.read_bytes() for p in game_paths.steam.rglob("*") if p.is_file()}
    if mode == "art":
        atomic(source / "assets/steam/portrait.png", b"new portrait")
    elif mode == "shortcut":
        doc["shortcuts"]["0"]["LaunchOptions"] = "old"
        atomic(shortcut, vdf.binary_dumps(doc))
    elif mode == "mapping":
        doc = steam.read_vdf(steam.mapping_path(game_paths))
        steam.mappings(doc)["0"]["name"] = "foreign"
        atomic(steam.mapping_path(game_paths), vdf.dumps(doc))
    monkeypatch.setattr(install, "__version__", "0.3.3")
    result = install.install(game_paths, source)
    assert result["steam_changes_pending"] is (mode != "engine")
    assert result["restart_required"] is (mode != "engine")
    assert result["shortcut_pending"] is (mode in ("shortcut", "art"))
    if mode == "engine":
        assert {p: p.read_bytes() for p in game_paths.steam.rglob("*") if p.is_file()} == before
        monkeypatch.setattr(steam.sys, "platform", "linux")
        monkeypatch.setattr(steam, "running", lambda: True)
        assert call(capsys, "apply", "--restart-steam") == {"applied": [], "steam_restarted": False}
    else:
        steam.apply(game_paths)
        assert vdf.binary_loads(shortcut.read_bytes())["shortcuts"]["0"]["tags"] == {"0": "Favorites"}


def test_install_prunes_redundant_game_enable_without_replacing_previous_mapping(game_paths, source):
    install.install(game_paths, source)
    saved = state(game_paths)
    previous = {"name": "proton_10", "priority": "42"}
    doc = steam.read_vdf(steam.mapping_path(game_paths))
    steam.mappings(doc)[APP] = previous
    atomic(steam.mapping_path(game_paths), vdf.dumps(doc))
    saved["games"][APP]["pending"] = "enable"
    save_state(game_paths, saved)
    steam.apply(game_paths)
    saved = state(game_paths)
    saved["games"][APP]["pending"] = "enable"
    save_state(game_paths, saved)
    assert install.install(game_paths, source)["steam_changes_pending"] is False
    assert state(game_paths)["games"][APP]["previous_mapping"] == previous


def test_install_prunes_foreign_game_disable_and_removes_only_managed_localfix(game_paths, source):
    install.install(game_paths, source)
    steam.apply(game_paths)
    managed = localfix.generate(game_paths, APP, {})
    foreign = game_paths.config / "localfixes/42.py"
    atomic(foreign, "# player localfix\n")
    saved = state(game_paths)
    saved["games"][APP].update(enabled=True, pending="disable")
    save_state(game_paths, saved)
    before = steam.mapping_path(game_paths).read_bytes()
    assert install.install(game_paths, source)["steam_changes_pending"] is False
    assert not managed.exists() and foreign.read_text() == "# player localfix\n"
    assert steam.mapping_path(game_paths).read_bytes() == before


def test_art_hashed_flat_grid_and_missing_are_absolute_existing_only(game_paths):
    cache = game_paths.steam / "appcache/librarycache"
    portrait = cache / APP / ("a" * 40) / "library_600x900.jpg"
    hero = cache / f"{APP}_library_hero.jpg"
    logo = cache / APP / ("b" * 40) / "logo.png"
    header = steam.shortcut_path(game_paths).parent / "grid" / f"{APP}.png"
    for file in (portrait, hero, logo, header):
        atomic(file, b"image")
    # A directory named like artwork must not be returned.
    (cache / APP / ("a" * 40) / "header.jpg").mkdir()
    result = cli.game(game_paths, APP)["art"]
    assert result == dict(portrait=str(portrait.resolve()), hero=str(hero.resolve()),
                          header=str(header.resolve()), logo=str(logo.resolve()))
    assert cli.game(game_paths, "42")["art"] == dict(portrait=None, hero=None, header=None, logo=None)
    shortcut_id = "3140405199"
    grid = steam.shortcut_path(game_paths).parent / "grid"
    for suffix in ("p", "_hero", "", "_logo"):
        atomic(grid / f"{shortcut_id}{suffix}.png", b"image")
    assert all(Path(p).is_file() for p in cli.game(game_paths, shortcut_id)["art"].values())


@pytest.mark.parametrize("recipe,expected,reason", [(True, "GE-Proton11-11", "pinned"),
    (True, "GE-Proton9-9", "recipe_fallback"), (False, "Proton 12.0", "newest"), (False, None, None)])
def test_proton_preview_matches_launch_without_side_effects(game_paths, monkeypatch, recipe, expected, reason):
    if expected:
        atomic(game_paths.tools / expected / "proton", "fake")
        if expected != "Proton 12.0":
            atomic(game_paths.steam / "steamapps/common/Proton 12.0/proton", "fake")
    if not recipe:
        (game_paths.recipes / f"{APP}.toml").unlink()
    before = {p: p.read_bytes() for p in game_paths.data.rglob("*") if p.is_file()}
    result = cli.game(game_paths, APP)["proton"]
    assert result == ({"name": expected, "reason": reason} if expected else None)
    assert {p: p.read_bytes() for p in game_paths.data.rglob("*") if p.is_file()} == before
    if expected:
        assert launch.resolve_proton(game_paths, recipes.load(game_paths, APP) or {}, net.Budget(), lambda _: None).name == expected


# Capture the actual detector before the autouse fixture replaces its entry point.
DETECTOR = getattr(steam, "game_running", None)


@pytest.mark.parametrize("env,args,expected", [(b"SteamAppId=2300320\0", b"wine\0game.exe\0", True),
    (b"SteamAppId=23003200\0", b"wine\0game.exe\0", False),
    (b"", b"/usr/bin/reaper\0SteamLaunch\0AppId=2300320\0--\0", True),
    (b"", b"/usr/bin/reaper\0SteamLaunch\0AppId=23003200\0--\0", False)])
def test_game_process_detection(tmp_path, env, args, expected):
    proc = tmp_path / "proc"
    atomic(proc / "123/environ", env)
    atomic(proc / "123/cmdline", args)
    assert DETECTOR is not None, "Immediate mod application needs a safe process detector"
    assert DETECTOR(APP, proc) is expected


def test_art_steamos_39_capsule_and_header_names(game_paths):
    # SteamOS 3.9 clients store library_capsule.jpg and library_header.jpg (Deck, 2026-11-01).
    cache = game_paths.steam / "appcache/librarycache" / APP
    capsule = cache / ("c" * 40) / "library_capsule.jpg"
    header = cache / ("d" * 40) / "library_header.jpg"
    for file in (capsule, header):
        atomic(file, b"image")
    art = cli.game(game_paths, APP)["art"]
    assert art["portrait"] == str(capsule.resolve()) and art["header"] == str(header.resolve())


def test_game_running_skips_processes_hiding_their_environment(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    hidden, game = proc / "100", proc / "200"
    for process in (hidden, game):
        (process).mkdir(parents=True)
        (process / "cmdline").write_bytes(b"x\0")
    (hidden / "environ").write_bytes(b"")
    (game / "environ").write_bytes(f"SteamAppId={APP}\0".encode())
    real = Path.read_bytes
    def read_bytes(self):
        if self == hidden / "environ":
            raise PermissionError("undumpable")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert DETECTOR(APP, proc) is True
    assert DETECTOR("999", proc) is False
