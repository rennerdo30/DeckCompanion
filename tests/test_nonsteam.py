"""Offline registration, preservation, handoff and runtime integrity regressions."""
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import tarfile
import pytest
import vdf
from deckcompanion import cli, content, install, net, recipes, splash, steam, update
from deckcompanion.common import Error, atomic, read_json, state, write_json


@pytest.fixture
def ns():
    from deckcompanion import nonsteam
    return nonsteam


@pytest.fixture
def runtime():
    from deckcompanion import umu
    return umu


def exe_file(tmp_path, name="My game.exe"):
    path = tmp_path / name
    path.write_bytes(b"MZ")
    return path


def test_umu_recipe_id_uses_shared_validation():
    recipe = {"schema_version": 1, "appid": "umu-42", "version": "1", "name": "Example"}
    assert recipes.validate(recipe, "umu-42") == recipe
    with pytest.raises(Error):
        recipes.validate(dict(recipe, appid="umu-../42"))


def test_nonsteam_pending_participates_in_existing_queue():
    assert steam.has_pending({"games": {}, "non_steam": {"example": {"pending": "add"}}})
    assert steam.has_pending({"games": {}, "non_steam": {"example": {"pending": "remove"}}})
    assert not steam.has_pending({"games": {}, "non_steam": {"example": {"pending": None}}})


def test_cli_register_list_remove_preserves_prefix(paths, tmp_path, monkeypatch, capsys):
    exe = exe_file(tmp_path)
    monkeypatch.setattr(steam, "running", lambda: True)
    assert cli.main(["add-game", "--exe", str(exe), "--name", "My Game", "--store", "gog", "--json"]) == 0
    import json
    registered = json.loads(capsys.readouterr().out)
    assert registered["kind"] == "non_steam" and registered["slug"] == "my-game"
    prefix = paths.data / "prefixes/my-game/pfx/drive_c/save.dat"
    atomic(prefix, "player save")
    assert cli.main(["games", "--json"]) == 0
    games = json.loads(capsys.readouterr().out)["games"]
    assert any(g["kind"] == "non_steam" for g in games if g.get("slug") == "my-game")
    assert not steam.shortcut_path(paths).exists()
    assert cli.main(["remove-game", "my-game", "--json"]) == 0
    assert prefix.read_text() == "player save"
    assert state(paths)["non_steam"]["my-game"]["pending"] == "remove"


def test_nonsteam_queue_uses_detached_apply(paths, ns, tmp_path, monkeypatch, capsys):
    from deckcompanion import apply_helper
    ns.add(paths, exe_file(tmp_path), "Example")
    monkeypatch.setattr(steam, "running", lambda: True)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(apply_helper, "schedule", lambda p: calls.append(p) or {"scheduled": True})
    assert cli.main(["apply", "--restart-steam", "--json"]) == 0
    assert calls == [paths] and '"scheduled": true' in capsys.readouterr().out
    assert state(paths)["non_steam"]["example"]["pending"] == "add"


def test_uninstall_removes_owned_shortcuts_and_keeps_saves(paths, ns, tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    ns.add(paths, exe_file(tmp_path), "Example")
    save = paths.data / "prefixes/example/pfx/save.dat"
    atomic(save, "player save")
    steam.apply(paths)
    install.uninstall(paths)
    assert not vdf.binary_loads(steam.shortcut_path(paths).read_bytes())["shortcuts"]
    assert save.read_text() == "player save"


def test_umu_cli_install_command(paths, runtime, monkeypatch, capsys):
    monkeypatch.setattr(runtime, "install", lambda p: {"installed": True, "version": runtime.VERSION})
    assert cli.main(["umu", "install", "--json"]) == 0
    assert '"installed": true' in capsys.readouterr().out


def test_apply_composes_ui_and_games_and_preserves_foreign(paths, ns, tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    path = steam.shortcut_path(paths)
    foreign = {"appid": -12, "Exe": '"other"', "AppName": "Other", "tags": {"0": "favorite"}}
    atomic(path, vdf.binary_dumps({"shortcuts": {"0": foreign}}))
    one = ns.add(paths, exe_file(tmp_path), "My Game")
    two = ns.add(paths, exe_file(tmp_path, "second.exe"), "My Game")
    assert one["slug"] != two["slug"] and one["appid"] != two["appid"]
    data = state(paths)
    data["shortcut_pending"] = True
    write_json(paths.data / "state.json", data)
    assert steam.has_pending(data)
    steam.apply(paths)
    entries = list(vdf.binary_loads(path.read_bytes())["shortcuts"].values())
    assert len(entries) == 4 and foreign in entries
    shortcut = next(e for e in entries if e.get("LaunchOptions") == "run-umu my-game")
    assert shortcut["Exe"] == '"' + str(paths.data / "bin/deckcompanion") + '"'
    assert shortcut["StartDir"] == '"' + str(tmp_path) + '"'
    assert shortcut["icon"] == str(paths.data / "assets/icon.png")
    assert not steam.has_pending(state(paths))
    ns.remove(paths, one["slug"])
    steam.apply(paths)
    entries = list(vdf.binary_loads(path.read_bytes())["shortcuts"].values())
    assert len(entries) == 3 and foreign in entries
    assert (paths.data / "prefixes/my-game").is_dir()


def test_running_steam_guard_and_apply_failure_keep_queue(paths, ns, tmp_path, monkeypatch):
    ns.add(paths, exe_file(tmp_path), "Example")
    monkeypatch.setattr(steam, "running", lambda: True)
    with pytest.raises(Error, match="close Steam"):
        steam.apply(paths)
    assert state(paths)["non_steam"]["example"]["pending"] == "add"
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(steam, "guarded_write", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        steam.apply(paths)
    assert state(paths)["non_steam"]["example"]["pending"] == "add"


def test_shortcut_collision_refuses_foreign_edit(paths, ns, tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    g = ns.add(paths, exe_file(tmp_path), "Example")
    path = steam.shortcut_path(paths)
    foreign = {"appid": int(g["appid"]) - 0x100000000, "Exe": "other", "AppName": "Other"}
    payload = vdf.binary_dumps({"shortcuts": {"0": foreign}})
    atomic(path, payload)
    with pytest.raises(Error, match="Preserving another"):
        steam.apply(paths)
    assert path.read_bytes() == payload
    assert steam.has_pending(state(paths))


def test_validation_duplicate_and_no_prefix_escape(paths, ns, tmp_path):
    exe = exe_file(tmp_path)
    for name in ("", "bad\nname"):
        with pytest.raises(Error):
            ns.add(paths, exe, name)
    with pytest.raises(Error):
        ns.add(paths, tmp_path / "missing.exe", "Missing")
    ns.add(paths, exe, "Example")
    with pytest.raises(Error, match="already registered"):
        ns.add(paths, exe, "Duplicate")
    with pytest.raises(Error):
        ns.remove(paths, "../outside")


def test_database_exact_unique_store_lookup(paths, runtime):
    atomic(paths.cache / "umu-database.csv", 'TITLE,STORE,CODENAME,UMU_ID\n"Game, One",gog,x,umu-42\nGame,egs,x,umu-50\nGame,egs,y,umu-51\n')
    assert runtime.lookup(paths, "Game, One", "gog") == "umu-42"
    assert runtime.lookup(paths, "Game, One", "egs") == "umu-default"
    assert runtime.lookup(paths, "Game", "egs") == "umu-default"
    assert runtime.lookup(paths, "Unknown", "none", "123") == "umu-123"
    with pytest.raises(Error):
        runtime.lookup(paths, "Unknown", "none", "../bad")


def test_umu_recipe_uses_existing_validator_and_library_is_numeric(paths, ns, runtime, tmp_path):
    paths = replace(paths, recipes=tmp_path / "recipes")
    atomic(paths.recipes / "umu-42.toml", 'schema_version=1\nappid="umu-42"\nversion="1"\nname="Example"\nnotes="Known game"\n')
    g = ns.add(paths, exe_file(tmp_path), "Example", umu_id="42")
    assert recipes.load(paths, "umu-42")["notes"] == "Known game"
    assert ns.game(paths, state(paths)["non_steam"][g["slug"]])["has_recipe"]
    result = cli.execute(cli.parser().parse_args(["games"]), paths)
    assert any(x.get("slug") == "example" for x in result["games"])
    with pytest.raises(Error):
        recipes.validate({"schema_version": 1, "appid": "umu-../bad"})


def test_run_umu_separate_exec_and_fail_open_optional(paths, ns, runtime, tmp_path, monkeypatch):
    exe = exe_file(tmp_path)
    ns.add(paths, exe, "Example", "egs", "42")
    monkeypatch.setattr(steam, "proton", lambda *a: [{"name": "Proton 12", "path": "/valve"}, {"name": "GE-Proton11-7", "path": "/ge"}])
    monkeypatch.setattr(runtime, "install", lambda *a: {"path": "/external/umu-run"})
    monkeypatch.setattr(content, "reconcile", lambda *a: (_ for _ in ()).throw(OSError("optional failure")))
    monkeypatch.setattr(ns.os, "chdir", lambda p: None)
    calls = []
    monkeypatch.setattr(splash, "show", lambda *a: calls.append(("splash", a[1])))
    monkeypatch.setattr(ns.os, "execve", lambda *a: calls.append(("exec", a)))
    ns.run(paths, "example", {"ORIGINAL": "keep"})
    _, (runner, args, env) = calls[-1]
    assert runner == "/external/umu-run" and args == [runner, str(exe)]
    assert env["GAMEID"] == "umu-42" and env["STORE"] == "egs" and env["PROTONPATH"] == str(Path("/ge"))
    assert env["WINEPREFIX"] == str(paths.data / "prefixes/example/pfx") and env["ORIGINAL"] == "keep"
    assert calls[0][0] == "splash" and calls[0][1]["name"] == "Example"
    assert "continuing launch" in (paths.data / "logs" / f"{state(paths)['non_steam']['example']['appid']}-last.log").read_text()


def test_run_umu_recipe_content_and_protected_environment(paths, ns, runtime, tmp_path, monkeypatch):
    ns.add(paths, exe_file(tmp_path), "Example")
    monkeypatch.setattr(steam, "proton", lambda *a: [{"name": "GE-Proton", "path": "/ge"}])
    monkeypatch.setattr(runtime, "install", lambda *a: {"path": "/external/umu-run"})
    monkeypatch.setattr(recipes, "load", lambda *a: {"version": "1", "runtime": {"env": {"GAMEID": "bad", "SETTING": "yes"}, "launch_options": ["--windowed"]}})
    seen = []
    monkeypatch.setattr(content, "reconcile", lambda *a: seen.append(a) or {})
    monkeypatch.setattr(content, "settings", lambda *a: None)
    monkeypatch.setattr(ns.os, "chdir", lambda *a: None)
    monkeypatch.setattr(splash, "show", lambda *a: None)
    monkeypatch.setattr(ns.os, "execve", lambda *a: seen.append(a))
    ns.run(paths, "example", {})
    assert seen[0][3] == paths.data / "prefixes/example"
    assert seen[-1][1][-1] == "--windowed" and seen[-1][2]["SETTING"] == "yes"
    assert seen[-1][2]["GAMEID"] == "umu-default"


def test_install_verifies_archive_and_license_atomically(paths, runtime, tmp_path, monkeypatch):
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w") as tar:
        info = tarfile.TarInfo("umu/umu-run")
        info.size = 8
        tar.addfile(info, io.BytesIO(b"#!zipapp"))
    archive = tmp_path / "release.tar"
    archive.write_bytes(blob.getvalue())
    license_file = tmp_path / "LICENSE"
    license_file.write_bytes(b"license fixture")
    monkeypatch.setattr(runtime, "SHA256", net.digest(archive))
    monkeypatch.setattr(runtime, "LICENSE_SHA256", net.digest(license_file))
    def download(url, target, budget):
        atomic(target, (archive if url == runtime.URL else license_file).read_bytes())
    monkeypatch.setattr(net, "fetch", download)
    result = runtime.install(paths)
    target = Path(result["path"])
    assert target.read_bytes() == b"#!zipapp" and (target.parent / "LICENSE").read_bytes() == b"license fixture"
    assert runtime.install(paths) == result
    target.write_bytes(b"changed")
    with pytest.raises(Error, match="Preserving changed"):
        runtime.install(paths)
    assert target.read_bytes() == b"changed"


def test_bad_umu_checksum_never_activates(paths, runtime, monkeypatch):
    monkeypatch.setattr(net, "fetch", lambda url, target, budget: atomic(target, b"corrupt"))
    with pytest.raises(Error, match="checksum"):
        runtime.install(paths)
    assert not (paths.data / "umu" / runtime.VERSION).exists()


def test_run_umu_real_recipe_mods_settings_and_preferences(paths, ns, runtime, tmp_path, monkeypatch):
    paths = replace(paths, recipes=tmp_path / "recipes")
    source = tmp_path / "mod.txt"
    source.write_text("mod content")
    atomic(paths.recipes / "umu-42.toml", f'''schema_version=1
appid="umu-42"
version="1"
name="Example"
[[mods]]
id="mod"
name="Mod"
version="1"
mode="copy"
target="pfx/mod.txt"
url="https://example.org/mod"
sha256="{net.digest(source)}"
[[settings]]
id="display"
target="pfx/game.ini"
format="ini"
section="Display"
key="fullscreen"
value="true"
''')
    game = ns.add(paths, exe_file(tmp_path), "Example", umu_id="42")
    prefix = paths.data / "prefixes/example/pfx"
    prefix.mkdir()
    monkeypatch.setattr(steam, "proton", lambda *a: [{"name":"GE-Proton", "path":"/ge"}])
    monkeypatch.setattr(runtime, "install", lambda *a: {"path":"/external/umu-run"})
    monkeypatch.setattr(net, "cached", lambda *a: source)
    monkeypatch.setattr(splash, "show", lambda *a: None)
    monkeypatch.setattr(ns.os, "chdir", lambda *a: None)
    monkeypatch.setattr(ns.os, "execve", lambda *a: None)
    ns.run(paths, "example", {})
    assert (prefix / "mod.txt").read_text() == "mod content"
    assert "fullscreen = true" in (prefix / "game.ini").read_text()
    cli.execute(cli.parser().parse_args(["mod",game["appid"],"mod","--off"]), paths)
    ns.run(paths, "example", {})
    assert not (prefix / "mod.txt").exists()
    assert ns.game(paths, state(paths)["non_steam"]["example"])["mods"][0]["state"] == "off"


def test_umu_exec_failure_logs_and_marks_handoff_failed(paths, ns, runtime, tmp_path, monkeypatch):
    game = ns.add(paths, exe_file(tmp_path), "Example")
    monkeypatch.setattr(steam, "proton", lambda *a: [{"name":"GE-Proton", "path":"/ge"}])
    monkeypatch.setattr(runtime, "install", lambda *a: {"path":"/external/umu-run"})
    monkeypatch.setattr(splash, "show", lambda *a: None)
    monkeypatch.setattr(ns.os, "chdir", lambda *a: None)
    monkeypatch.setattr(ns.os, "execve", lambda *a: (_ for _ in ()).throw(OSError("exec denied")))
    with pytest.raises(OSError):
        ns.run(paths, "example", {})
    assert state(paths)["non_steam"]["example"]["last_launch"]["ok"] is False
    assert "umu handoff failed" in (paths.data / "logs" / f"{game['appid']}-last.log").read_text()


def test_update_installs_umu_even_offline_and_reports_failure(paths, runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(update, "json_url", lambda *a: (_ for _ in ()).throw(Error("offline", "Offline")))
    monkeypatch.setattr(runtime, "install", lambda p: calls.append(p) or {"installed": True})
    assert update.update(paths)["umu"]["installed"] and calls == [paths]
    monkeypatch.setattr(runtime, "install", lambda p: (_ for _ in ()).throw(Error("offline", "Offline")))
    assert update.update(paths)["umu"] == {"installed": False, "error": "Offline"}
