"""Deck-reported shortcuts, using isolated files and mocked Steam only."""
from copy import deepcopy

import pytest
import vdf

from deckcompanion import apply_helper, cli, install, nonsteam, steam, takeover
from deckcompanion.common import atomic, save_state, state
from test_shortcut_diff import release  # Minimal synthetic installer payload.


UI_IDS = [3140405199, 3748988868, 4215959755, 3865338752, 2506537342]


def invoke(paths, *args):
    return cli.execute(cli.parser().parse_args([*args, "--json"]), paths)


def entry(ident, name, exe, start="/home/deck/Downloads", **extra):
    return {"appid": ident - 2**32 if ident >= 2**31 else ident,
            "appname": name, "exe": exe, "StartDir": start, "tags": {}, **extra}


def write_entries(paths, entries, path=None):
    path = path or steam.shortcut_path(paths)
    atomic(path, vdf.binary_dumps({"shortcuts": {str(i * 7): e for i, e in enumerate(entries)}}))
    return path


def read_entries(path):
    return list(vdf.binary_loads(path.read_bytes())["shortcuts"].values())


def write_mappings(paths, entries):
    doc = {"unrelated": "preserve"}
    steam.mappings(doc).update(entries)
    atomic(steam.mapping_path(paths), vdf.dumps(doc))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(steam.subprocess, "Popen", lambda *a, **k: pytest.fail("No real process"))


@pytest.fixture
def deck(paths):
    windows = entry(3135360851, "Battle.net", '"/home/deck/pfx/Battle.net Launcher.exe"',
                    LastPlayTime=1793530000)
    native = [entry(3118654025, "renner.dev Launcher", '"/home/deck/Downloads/launcher.AppImage"'),
              entry(3701022597, "AnimaSky", '"/home/deck/Downloads/launcher.AppImage"'),
              entry(2289183252, "Devkit Game: AnimaSky_proton_7eef", '"/home/deck/devkit-game/AnimaSky_proton_7eef"'),
              entry(2672769357, "Devkit Game: AnimaSky_proton_9aec", '"/home/deck/devkit-game/AnimaSky_proton_9aec"')]
    own = [entry(i, "deckcompanion", str(paths.data / "bin/deckcompanion-ui").upper()) for i in UI_IDS]
    path = write_entries(paths, [windows, *native, *own])
    originals = {"3135360851": {"name": "Proton-GE Latest", "config": "custom", "priority": "17", "extra": {"keep": "yes"}},
                 "2289183252": {"name": "proton-stable"}, "2672769357": {"name": "proton-stable"},
                 **{str(i): {"name": "DeckCompanion"} for i in UI_IDS}}
    write_mappings(paths, originals)
    # Stale prior engine state caused the phantom numeric DeckCompanion game.
    save_state(paths, {"games": {str(UI_IDS[0]): {"enabled": True}}})
    for suffix in ("", "p", "_hero", "_logo", "_icon"):
        atomic(path.parent / "grid" / f"3135360851{suffix}.png", suffix.encode())
    atomic(paths.tools / "Proton 11.0/proton", "fixture")
    atomic(paths.tools / "GE-Proton11-7/proton", "fixture")
    return path, originals


def test_deck_library_lists_every_user_shortcut_and_hides_our_numeric_game(paths, deck, capsys):
    import json
    before = (paths.data / "state.json").read_bytes()
    assert cli.main(["games", "--json"]) == 0
    rows = {g["appid"]: g for g in json.loads(capsys.readouterr().out)["games"]}
    assert (paths.data / "state.json").read_bytes() == before
    expected = {"3135360851", "3118654025", "3701022597", "2289183252", "2672769357"}
    assert expected <= rows.keys()
    assert not set(map(str, UI_IDS)) & rows.keys()
    windows = rows["3135360851"]
    assert windows == {
        "appid": "3135360851", "name": "Battle.net", "installed": True, "kind": "steam_shortcut",
        "exe": "/home/deck/pfx/Battle.net Launcher.exe", "start_dir": "/home/deck/Downloads",
        "has_recipe": False, "enabled": False, "pending": None, "recipe_version": None,
        "mods": [], "last_launch": None, "last_played": 1793530000,
        "art": {name: str((deck[0].parent / "grid" / f"3135360851{suffix}.png").resolve())
                for name, suffix in (("portrait", "p"), ("header", ""), ("hero", "_hero"), ("logo", "_logo"), ("icon", "_icon"))},
        "takeover": "on", "previous_mapping": "Proton-GE Latest", "valve_recommendation": None,
        "options": {"proton": True, "takeover": True}, "community_fix": None, "community_fix_source": None,
        "proton": {"name": "GE-Proton11-7", "reason": "newest"}}
    assert invoke(paths, "game", "3135360851") == dict(windows, notes="", log_tail=[])


@pytest.mark.parametrize("exe,mapped", [("native", False), ("launch.sh", False),
    ("game.AppImage", False), ("AnimaSky_proton_7eef", True)])
def test_native_and_proton_mapped_shortcuts_have_exact_capabilities(paths, exe, mapped):
    write_entries(paths, [entry(3118654025, "Native", f'"/home/deck/{exe}"')])
    if mapped:
        write_mappings(paths, {"3118654025": {"name": "proton-stable"}})
    atomic(paths.tools / "Proton 11.0/proton", "fixture")
    result = invoke(paths, "game", "3118654025")
    assert result["kind"] == "steam_shortcut" and result["installed"]
    assert result["options"] == {"proton": mapped, "takeover": False}
    assert result["takeover"] == "n/a" and result["last_played"] is None
    assert result["proton"] == ({"name": "Proton 11.0", "reason": "user_override"} if mapped else None)


@pytest.mark.parametrize("spelling", ["exe", "EXE", "Exe"])
def test_windows_shortcuts_capture_exact_first_mapping_and_restore(paths, spelling):
    value = entry(3135360851, "Battle.net", "unused", LastPlayTime=123)
    value.pop("exe")
    value[spelling] = "'/games/Battle.NET.EXE'"
    path = write_entries(paths, [value])
    original = {"name": "Proton-GE Latest", "priority": "19", "extra": {"nested": "keep"}}
    write_mappings(paths, {"3135360851": original})
    shortcut_before = path.read_bytes()
    invoke(paths, "settings", "--set", "takeover_all=on")
    saved = state(paths)
    assert saved["games"]["3135360851"]["pending"] == "enable"
    assert saved["takeover_snapshot"]["entries"]["3135360851"] == original
    steam.apply(paths)
    row = invoke(paths, "game", "3135360851")
    assert row["kind"] == "steam_shortcut" and row["previous_mapping"] == original["name"]
    invoke(paths, "game-option", "3135360851", "--takeover", "off")
    steam.apply(paths)
    assert invoke(paths, "game", "3135360851")["takeover"] == "excluded"
    invoke(paths, "game-option", "3135360851", "--takeover", "on")
    steam.apply(paths)
    assert state(paths)["takeover_snapshot"]["entries"]["3135360851"] == original
    invoke(paths, "restore-all")
    steam.apply(paths)
    assert steam.mappings(steam.read_vdf(steam.mapping_path(paths)))["3135360851"] == original
    assert path.read_bytes() == shortcut_before
    assert invoke(paths, "restore-all") == {"restoring": 0, "pending": []}


def test_active_account_artwork_and_timestamp_come_from_shortcuts(paths, deck):
    other = paths.steam / "userdata/456/config/shortcuts.vdf"
    write_entries(paths, [entry(3000000001, "Other account", '"/games/other.exe"')], other)
    saved = state(paths)
    saved["games"]["3000000001"] = {"enabled": True, "previous_mapping": None}
    save_state(paths, saved)
    # Conflicting caches must not override this shortcut's own grid/LastPlayTime.
    atomic(paths.steam / "appcache/librarycache/3135360851_header.png", b"wrong")
    atomic(deck[0].parent / "localconfig.vdf", vdf.dumps({"UserLocalConfigStore": {"Software": {
        "Valve": {"Steam": {"apps": {"3135360851": {"LastPlayed": "99"}}}}}}}))
    rows = {g["appid"]: g for g in invoke(paths, "games")["games"]}
    assert "3000000001" not in rows
    assert rows["3135360851"]["last_played"] == 1793530000
    assert rows["3135360851"]["art"]["header"] == str((deck[0].parent / "grid/3135360851.png").resolve())
    assert "3000000001" in takeover.discover(paths)[1]  # Preserve machine-wide mapping discovery.


def test_registered_umu_wrapper_is_listed_once_beside_native_shortcut(paths, monkeypatch):
    exe = paths.data / "game.exe"
    atomic(exe, "fixture")
    saved = nonsteam.add(paths, str(exe), "Managed", "none", "umu-default")
    registry = state(paths)["non_steam"][saved["slug"]]
    payload = nonsteam.shortcut_bytes(paths, vdf.binary_dumps({"shortcuts": {}}), registry)
    wrapper = next(iter(vdf.binary_loads(payload)["shortcuts"].values()))
    write_entries(paths, [wrapper, entry(3701022597, "Native", '"/games/native.AppImage"')])
    rows = invoke(paths, "games")["games"]
    assert sum(g["appid"] == saved["appid"] for g in rows) == 1
    assert next(g for g in rows if g["appid"] == saved["appid"])["kind"] == "non_steam"
    assert next(g for g in rows if g["appid"] == "3701022597")["kind"] == "steam_shortcut"


@pytest.mark.parametrize("quote,case,relative", [('"', False, False), ("'", True, False),
    ("", True, False), ('"', True, True)])
def test_repeated_installs_replace_path_identity_preserving_current_steam_fields(paths, release, quote, case, relative):
    install.install(paths, release)
    steam.apply(paths)
    current = steam.shortcut_identity(paths)[1]
    exe = "deckcompanion-ui" if relative else str(paths.data / "bin/deckcompanion-ui")
    directory = str(paths.data / "bin")
    if case:
        exe, directory = exe.upper(), directory.upper()
    duplicates = [entry(i, "DECKCOMPANION", quote + exe + quote, quote + directory + quote,
                        LastPlayTime=100 + n) for n, i in enumerate(UI_IDS) if i != current]
    kept = steam.shortcut_desired(paths)
    kept.update(LastPlayTime=1793530000, tags={"0": "Favorites"}, AllowOverlay=0)
    foreign = entry(777, "DeckCompanion", '"/other/user-ui"')
    path = write_entries(paths, [*duplicates, foreign, kept])
    for i in UI_IDS:
        for suffix in steam.ARTWORK.values():
            atomic(path.parent / "grid" / f"{i}{suffix}.png", b"old grid")
    write_mappings(paths, {str(i): {"name": "proton-stable"} for i in UI_IDS} | {"777": {"name": "keep"}})
    data = state(paths)
    data["games"][str(UI_IDS[0])] = {"enabled": True}
    data["takeover_snapshot"] = {"taken_at": "2026-11-01T14:00:00Z", "default_managed": False,
                                "entries": {str(UI_IDS[0]): {"name": "proton-stable"}}}
    save_state(paths, data)
    before = {p: p.read_bytes() for p in path.parent.glob("grid/*.png")}
    source_before = path.read_bytes()
    result = install.install(paths, release)
    assert result["shortcut_pending"] and result["steam_changes_pending"]
    assert any(r["field"] == "duplicates" for r in result["steam_change_reasons"])
    assert path.read_bytes() == source_before
    assert all(p.read_bytes() == b for p, b in before.items())
    steam.apply(paths)
    entries = read_entries(path)
    assert len(entries) == 2 and foreign in entries
    actual = next(e for e in entries if steam.shortcut_owned(paths, e))
    assert actual == kept
    for i in set(UI_IDS) - {current}:
        assert not any((path.parent / "grid" / f"{i}{s}.png").exists() for s in steam.ARTWORK.values())
        assert str(i) not in steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    assert steam.mappings(steam.read_vdf(steam.mapping_path(paths)))["777"] == {"name": "keep"}
    assert not set(map(str, UI_IDS)) & {g["appid"] for g in invoke(paths, "games")["games"]}
    # Introduce another historical appid on each install, with the same executable.
    for old in UI_IDS[:3]:
        if old == current:
            continue
        changed = deepcopy(actual)
        changed["appid"] = old - 2**32
        changed["Exe"] = str(paths.data / "bin/deckcompanion-ui")
        write_entries(paths, [foreign, changed])
        install.install(paths, release)
        steam.apply(paths)
        entries = read_entries(path)
        assert len(entries) == 2 and sum(steam.shortcut_owned(paths, e) for e in entries) == 1
        assert steam.shortcut_field(next(e for e in entries if steam.shortcut_owned(paths, e)), "LastPlayTime") == 1793530000
    assert not install.install(paths, release)["steam_changes_pending"]
    invoke(paths, "restore-all")
    steam.apply(paths)
    assert str(UI_IDS[0]) not in steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    assert state(paths)["takeover_snapshot"]["entries"][str(UI_IDS[0])] == {"name": "proton-stable"}


@pytest.mark.parametrize("detached", [False, True])
def test_duplicate_deletions_are_backed_up_verified_and_rolled_back(paths, release, monkeypatch, detached):
    install.install(paths, release)
    steam.apply(paths)
    path = steam.shortcut_path(paths)
    own = next(iter(vdf.binary_loads(path.read_bytes())["shortcuts"].values()))
    duplicate = dict(own, appid=UI_IDS[0] - 2**32)
    write_entries(paths, [duplicate, own])
    art = path.parent / "grid" / f"{UI_IDS[0]}_icon.png"
    atomic(art, b"original pixels")
    write_mappings(paths, {str(UI_IDS[0]): {"name": "proton-stable", "extra": "preserve"}})
    install.install(paths, release)
    originals = {p: p.read_bytes() for p in (path, art, steam.mapping_path(paths))}
    plan = steam.prepare_apply(paths)
    assert art in plan["originals"] and art in plan["deletes"]
    assert plan["mappings"][str(UI_IDS[0])] is None
    if detached:
        steam.backup_apply(plan)
        steam.commit_apply(paths, plan, closed_window=True)
        assert steam.verify_apply(paths, plan)
        assert list(art.parent.glob(art.name + ".deckcompanion-*.bak"))[0].read_bytes() == originals[art]
        atomic(art, b"Steam restored stale art")
        assert not steam.verify_apply(paths, plan)
        steam.discard_prepared(plan)
    else:
        monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            steam.commit_apply(paths, plan)
        assert all(p.read_bytes() == b for p, b in originals.items())
        assert state(paths)["shortcut_pending"]


@pytest.mark.parametrize("resurrect", [False, True])
def test_duplicate_cleanup_runs_in_existing_detached_helper(paths, release, monkeypatch, resurrect):
    install.install(paths, release)
    steam.apply(paths)
    path = steam.shortcut_path(paths)
    own = next(iter(vdf.binary_loads(path.read_bytes())["shortcuts"].values()))
    write_entries(paths, [dict(own, appid=UI_IDS[0] - 2**32), own])
    art = path.parent / "grid" / f"{UI_IDS[0]}.png"
    atomic(art, b"old")
    write_mappings(paths, {str(UI_IDS[0]): {"name": "DeckCompanion"}})
    install.install(paths, release)
    running = {"steam": True}
    monkeypatch.setattr(steam, "running", lambda: running["steam"])
    monkeypatch.setattr(apply_helper, "game_mode", lambda: False)
    monkeypatch.setattr(steam, "run", lambda *a, **k: running.update(steam=False))
    attempts = []
    def restore(*args):
        running["steam"] = True
        attempts.append(True)
        if resurrect and len(attempts) == 1:
            # Steam kept the repaired shortcut but restored just the stale art/mapping.
            atomic(art, b"restored by Steam")
            doc = steam.read_vdf(steam.mapping_path(paths))
            steam.mappings(doc)[str(UI_IDS[0])] = {"name": "DeckCompanion"}
            atomic(steam.mapping_path(paths), vdf.dumps(doc))
    monkeypatch.setattr(apply_helper, "restore", restore)
    receipt = apply_helper.apply_helper(paths)
    assert receipt["verified"] and not receipt["errors"] and receipt["steam_restarted"]
    assert {"appid": str(UI_IDS[0]), "action": "remove_shortcut"} in receipt["applied"]
    assert not art.exists() and len(read_entries(path)) == 1
    assert not state(paths)["shortcut_pending"]
    assert len(attempts) == (2 if resurrect else 1)
    assert str(UI_IDS[0]) not in steam.mappings(steam.read_vdf(steam.mapping_path(paths)))


@pytest.mark.parametrize("current_id", [True, False])
def test_duplicate_entries_even_with_same_appid_keep_exactly_one(paths, release, current_id):
    install.install(paths, release)
    steam.apply(paths)
    path = steam.shortcut_path(paths)
    first = steam.shortcut_desired(paths)
    if not current_id:
        first["appid"] = UI_IDS[0] - 2**32
    first.update(LastPlayTime=123, tags={"0": "Favorites"})
    second = dict(first, LastPlayTime=456)
    write_entries(paths, [first, second])
    result = install.install(paths, release)
    assert result["shortcut_pending"]
    steam.apply(paths)
    entries = read_entries(path)
    assert len(entries) == 1
    assert steam.shortcut_value("appid", entries[0]["appid"]) == steam.shortcut_identity(paths)[1]
    assert entries[0]["LastPlayTime"] == 123 and entries[0]["tags"] == first["tags"]
    assert not install.install(paths, release)["steam_changes_pending"]
