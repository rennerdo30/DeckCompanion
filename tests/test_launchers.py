"""No network, Wine, Steam, installer or systemd process is started by these tests."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import time
import pytest
import vdf
from deckcompanion import cli, content, net, nonsteam, recipes, splash, steam, umu
from deckcompanion.common import Error, atomic, locked, read_json, state, write_json


@pytest.fixture
def launchers():
    return importlib.import_module("deckcompanion.launchers")


@pytest.fixture
def service(launchers, monkeypatch):
    monkeypatch.setattr(launchers.sys, "platform", "linux")
    monkeypatch.setattr(launchers.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    monkeypatch.setattr(steam, "run", lambda cmd, timeout: calls.append((cmd, timeout)) or b"")
    return calls


@pytest.fixture
def installer(paths, launchers, monkeypatch):
    calls = []
    monkeypatch.setattr(steam, "proton", lambda *a: [{"name": "GE-Proton11-7", "path": "/ge"}])
    monkeypatch.setattr(umu, "install", lambda *a: {"path": "/external/umu-run"})
    def fetch(url, target, budget, max_bytes):
        assert url.startswith("https://") and max_bytes == 512 * 1024**2
        assert 0 < budget.remaining() <= launchers.DOWNLOAD_TIMEOUT
        atomic(target, b"MZfixture" if target.suffix == ".exe" else bytes.fromhex("d0cf11e0a1b11ae1") + b"fixture")
        calls.append(("download", url, target))
    monkeypatch.setattr(net, "fetch", fetch)
    class Process:
        pid = 8123
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            calls.append(("process", cmd, kwargs))
        def wait(self, timeout):
            assert timeout == launchers.INSTALL_TIMEOUT
            for p in content.launchers(paths):
                if p["store"] == calls[-1][2]["env"]["STORE"]:
                    atomic(launchers.prefix_path(paths, p["id"]) / p["installed_exe"], b"MZinstalled")
            return 0
    monkeypatch.setattr(launchers.subprocess, "Popen", Process)
    return calls


def test_cli_catalog_is_read_only_and_has_five_presets(paths, launchers, capsys):
    assert cli.main(["launchers", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [p["id"] for p in result["launchers"]] == ["epic", "gog", "ea", "ubisoft", "battlenet"]
    for p in result["launchers"]:
        assert p["installer_url"].startswith("https://")
        assert p["gameid"] == "umu-default" and p["checksum_sha256"] is None
        assert p["state"] == "not_installed" and not p["installed"] and p["job"] is None
        assert p["prefix"] == str(paths.data / "prefixes" / ("launcher-" + p["id"]))
    assert not (paths.data / "state.json").exists()
    assert not steam.shortcut_path(paths).exists()
    assert not paths.cache.exists()


@pytest.mark.parametrize("ident", ["epic", "gog", "ea", "ubisoft", "battlenet"])
def test_installer_registers_same_prefix_and_queues_apply(paths, launchers, service, installer, ident, monkeypatch):
    job = launchers.schedule(paths, ident)
    assert job["scheduled"] and job["launcher"]["state"] == "installing"
    token = job["launcher"]["job"]["token"]
    command, timeout = service[0]
    assert timeout == 5 and "--user" in command and "--property=RuntimeMaxSec=1560" in command
    assert "--property=KillMode=control-group" in command
    assert command[-4:] == ["launcher-helper", ident, token, "--json"]
    # Worker must not require the state lock during expensive work.
    original = net.fetch
    def fetch(*args, **kwargs):
        with locked(paths):
            pass
        return original(*args, **kwargs)
    monkeypatch.setattr(net, "fetch", fetch)
    result = launchers.run(paths, ident, token)["launcher"]
    assert result["state"] == "installed" and result["shortcut_pending"] == "add"
    assert result["job"]["pid"] and result["job"]["installer_pid"] == 8123
    payload = b"MZfixture" if result["installer_type"] == "exe" else bytes.fromhex("d0cf11e0a1b11ae1") + b"fixture"
    assert result["job"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["job"]["downloaded_bytes"] == len(payload)
    saved = state(paths)["non_steam"]["launcher-" + ident]
    assert nonsteam.roots(paths, saved)[0] == Path(result["prefix"])
    process = next(c for c in installer if c[0] == "process")
    assert process[2]["env"]["WINEPREFIX"] == str(Path(result["prefix"]) / "pfx")
    assert process[2]["env"]["PROTONPATH"] == str(Path("/ge"))
    assert process[2]["env"]["STORE"] == result["store"]
    assert process[1][0] == "/external/umu-run"
    if ident == "epic":
        assert process[1][1:3] == ["msiexec", "/i"]
        assert process[1][-2:] == ["/qn", "/norestart"]
    assert not list((paths.cache / "launcher-installers").glob("*"))
    assert not steam.shortcut_path(paths).exists()
    monkeypatch.setattr(steam, "running", lambda: False)
    steam.apply(paths)
    shortcut = next(iter(vdf.binary_loads(steam.shortcut_path(paths).read_bytes())["shortcuts"].values()))
    assert shortcut["LaunchOptions"] == "run-umu launcher-" + ident
    assert not launchers.schedule(paths, ident)["scheduled"]


def test_duplicate_and_stale_worker_cannot_override_job(paths, launchers, service):
    job = launchers.schedule(paths, "epic")["launcher"]["job"]
    with pytest.raises(Error, match="already installing"):
        launchers.schedule(paths, "epic")
    with pytest.raises(Error, match="no longer active"):
        launchers.run(paths, "epic", "wrong-token")
    assert launchers.job_status(paths, "epic")["token"] == job["token"]
    with pytest.raises(Error, match="Wait for launcher"):
        launchers.remove(paths, "epic", True)


def test_cli_install_worker_remove_envelopes(paths, launchers, service, installer, capsys):
    assert cli.main(["launcher", "install", "ea", "--json"]) == 0
    scheduled = json.loads(capsys.readouterr().out)
    assert scheduled["scheduled"] and scheduled["launcher"]["state"] == "installing"
    assert cli.main(["launcher-helper", "ea", scheduled["launcher"]["job"]["token"], "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    assert finished["launcher"]["state"] == "installed"
    assert cli.main(["launcher", "remove", "ea", "--purge", "--json"]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] and not removed["prefix_preserved"] and removed["steam_changes_pending"]
    assert removed["launcher"]["state"] == "not_installed"


def test_schedule_failure_and_expired_job_are_pollable(paths, launchers, service, monkeypatch):
    def failure(*args, **kwargs):
        raise Error("process_failed", "service refused")
    monkeypatch.setattr(steam, "run", failure)
    with pytest.raises(Error):
        launchers.schedule(paths, "ea")
    assert launchers.info(paths, "ea")["state"] == "failed"
    launchers.report(paths, "ea", active=True, started=time.time() - 1600)
    result = launchers.info(paths, "ea")
    assert result["state"] == "failed" and not result["job"]["active"]
    assert "expired" in result["job"]["error"]


@pytest.mark.parametrize("failure", ["download", "format", "empty", "size", "checksum", "exit", "missing", "timeout"])
def test_failure_never_registers_and_preserves_prefix(paths, launchers, service, installer, monkeypatch, failure):
    queued = launchers.schedule(paths, "ea")["launcher"]["job"]
    save = launchers.prefix_path(paths, "ea") / "pfx/player-save"
    atomic(save, "keep")
    if failure in ("download", "format", "empty", "size", "checksum"):
        def download(url, target, budget, max_bytes):
            if failure == "download":
                raise Error("network_timeout", "download timeout")
            atomic(target, b"html error" if failure == "format" else b"" if failure == "empty" else b"MZfixture")
        monkeypatch.setattr(net, "fetch", download)
        if failure == "size":
            monkeypatch.setattr(launchers, "LIMIT", 1)
        if failure == "checksum":
            original = launchers.preset
            monkeypatch.setattr(launchers, "preset", lambda p, i: dict(original(p, i), checksum_sha256="0" * 64))
    else:
        class Process:
            pid = 9000
            killed = False
            def __init__(self, *args, **kwargs):
                pass
            def wait(self, timeout):
                if failure == "timeout" and not self.killed:
                    raise subprocess.TimeoutExpired("installer", timeout)
                return 23 if failure == "exit" else 0
            def kill(self):
                self.killed = True
                assert self.pid in steam.STARTED_PIDS
        monkeypatch.setattr(launchers.subprocess, "Popen", Process)
    with pytest.raises((Error, subprocess.TimeoutExpired)):
        launchers.run(paths, "ea", queued["token"])
    result = launchers.info(paths, "ea")
    assert result["state"] == "failed" and not result["job"]["active"] and result["job"]["error"]
    assert not state(paths).get("non_steam") and save.read_text() == "keep"
    assert not list((paths.cache / "launcher-installers").glob("*"))


def test_remove_reinstall_and_purge_keep_identity_and_preferences(paths, launchers, service, installer, monkeypatch):
    token = launchers.schedule(paths, "gog")["launcher"]["job"]["token"]
    original = launchers.run(paths, "gog", token)["launcher"]
    save = Path(original["prefix"]) / "pfx/save"
    atomic(save, "player save")
    args = cli.parser().parse_args(["game-option", original["appid"], "--proton", "Custom"])
    cli.execute(args, paths)
    assert launchers.remove(paths, "gog")["prefix_preserved"]
    assert save.read_text() == "player save" and not launchers.info(paths, "gog")["installed"]
    before = len(installer)
    token = launchers.schedule(paths, "gog")["launcher"]["job"]["token"]
    reinstalled = launchers.run(paths, "gog", token)["launcher"]
    assert reinstalled["appid"] == original["appid"] and len(installer) == before
    assert state(paths)["non_steam"]["launcher-gog"]["proton_override"] == "Custom"
    assert not launchers.remove(paths, "gog", True)["prefix_preserved"]
    assert not Path(original["prefix"]).exists()
    assert state(paths)["non_steam"]["launcher-gog"]["pending"] == "remove"


def test_local_artwork_uses_guarded_apply(paths, launchers, monkeypatch):
    p = launchers.preset(paths, "epic")
    atomic(launchers.prefix_path(paths, "epic") / p["installed_exe"], b"MZ")
    portrait = paths.data / "assets/launchers/epic/portrait.png"
    icon = portrait.with_name("icon.png")
    atomic(portrait, b"local portrait")
    atomic(icon, b"local icon")
    game = launchers.register(paths, "epic")
    assert game["art"]["portrait"] == str(portrait)
    monkeypatch.setattr(steam, "running", lambda: True)
    with pytest.raises(Error):
        steam.apply(paths)
    target = steam.shortcut_path(paths).parent / "grid" / (game["appid"] + "p.png")
    assert not target.exists()
    monkeypatch.setattr(steam, "running", lambda: False)
    steam.apply(paths)
    assert target.read_bytes() == portrait.read_bytes()
    shortcut = next(iter(vdf.binary_loads(steam.shortcut_path(paths).read_bytes())["shortcuts"].values()))
    assert shortcut["icon"] == str(icon)


def test_unsafe_id_and_detach_platform_return_json(paths, launchers, capsys, monkeypatch):
    for args in (["launcher", "remove", "../outside", "--json"],
                 ["launcher", "install", "ea", "--purge", "--json"]):
        assert cli.main(args) == 1
        assert json.loads(capsys.readouterr().out)["error"]["code"] in ("unknown_launcher", "invalid_arguments")
    monkeypatch.setattr(launchers.sys, "platform", "win32")
    assert cli.main(["launcher", "install", "ea", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "detach_unavailable"


def test_prefix_redirect_and_purge_alias_are_rejected(paths, launchers, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    atomic(outside / "save", "keep")
    # Simulate a resolved link on Windows too, without requiring symlink privileges.
    monkeypatch.setattr(launchers, "prefix_path", lambda *a: outside)
    with pytest.raises(Error, match="redirected"):
        launchers.remove(paths, "epic", True)
    assert (outside / "save").read_text() == "keep"


def test_nonsteam_proton_option_reaches_exec_and_auto_restores_ge(paths, tmp_path, monkeypatch):
    atomic(tmp_path / "game.exe", b"MZ")
    game = nonsteam.add(paths, tmp_path / "game.exe", "Game")
    builds = [{"name": "Proton 12", "path": "/valve"}, {"name": "GE-Proton11-7", "path": "/ge"}]
    monkeypatch.setattr(steam, "proton", lambda *a: builds)
    monkeypatch.setattr(umu, "install", lambda *a: {"path": "/external/umu-run"})
    monkeypatch.setattr(nonsteam.os, "chdir", lambda *a: None)
    calls = []
    monkeypatch.setattr(nonsteam.os, "execve", lambda *a: calls.append(a))
    monkeypatch.setattr(splash, "show", lambda *a: None)
    monkeypatch.setattr(content, "reconcile", lambda *a: {})
    for choice, expected, reason in (("Proton 12", "/valve", "user_override"), ("auto", "/ge", "recipe_fallback")):
        assert cli.main(["game-option", game["appid"], "--proton", choice, "--json"]) == 0
        saved = state(paths)["non_steam"]["game"]
        assert saved["proton_override"] == (None if choice == "auto" else choice)
        assert nonsteam.game(paths, saved)["proton"]["reason"] == reason
        nonsteam.run(paths, "game", {})
        assert calls[-1][2]["PROTONPATH"] == str(Path(expected))
    assert game["appid"] not in state(paths)["games"]
    assert cli.main(["game-option", game["appid"], "--takeover", "on", "--json"]) == 1


def test_nonsteam_override_wins_recipe_pin_and_missing_override_falls_back(paths, tmp_path, monkeypatch):
    atomic(tmp_path / "game.exe", b"MZ")
    g = nonsteam.add(paths, tmp_path / "game.exe", "Game")
    monkeypatch.setattr(steam, "proton", lambda *a: [
        {"name": "GE-Proton11", "path": "/ge"}, {"name": "Proton 12", "path": "/valve"}])
    monkeypatch.setattr(recipes, "load", lambda *a: {"runtime": {"proton": "GE-Proton11"}})
    for choice, expected in (("Proton 12", "Proton 12"), ("missing", "GE-Proton11"), ("auto", "GE-Proton11")):
        result = cli.execute(cli.parser().parse_args(["game-option", g["appid"], "--proton", choice]), paths)
        assert result["proton"]["name"] == expected


def test_every_game_object_has_capabilities(paths, tmp_path):
    atomic(tmp_path / "game.exe", b"MZ")
    g = nonsteam.add(paths, tmp_path / "game.exe", "Game")
    assert cli.game(paths, g["appid"])["options"] == {"proton": True, "takeover": False}
    assert cli.game(paths, "2300320")["options"] == {"proton": True, "takeover": True}
    result = cli.execute(cli.parser().parse_args(["games"]), paths)
    assert all(g["options"] == {"proton": True, "takeover": g.get("kind") != "non_steam"} for g in result["games"])


def test_catalog_cache_prefers_valid_content_without_engine_change(paths, caplog):
    caplog.set_level("INFO")
    # Use independent data; no Python preset names or URLs may constrain the channel.
    text = '''schema_version=1
version="2"
[[launchers]]
id="new-store"
name="New Store"
store="new-store"
gameid="umu-123"
source_url="https://vendor.example/download"
installer_url="https://vendor.example/setup.exe"
installer_type="exe"
installer_args=["/quiet"]
unattended=true
installed_exe="pfx/drive_c/New Store/Store.exe"
checksum_sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
'''
    write_json(paths.cache / "index.json", {"launchers": {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}})
    assert content.launchers(paths)[0]["id"] == "new-store"
    assert content.launchers(paths)[0]["gameid"] == "umu-123"
    assert "source: cached" in caplog.text
    from deckcompanion import launchers
    assert launchers.listing(paths)["launchers"][0]["name"] == "New Store"


@pytest.mark.parametrize("broken", ["hash", "parse", "schema", "path", "http", "args", "duplicate", "id", "empty"])
def test_bad_cached_catalog_falls_back_and_logs_source(paths, caplog, broken):
    caplog.set_level("INFO")
    source = Path(__file__).resolve().parents[1] / "content/launchers.toml"
    text = source.read_text(encoding="utf-8")
    if broken == "parse": text = "[[bad"
    if broken == "schema": text = text.replace("schema_version = 1", "schema_version = 999")
    if broken == "path": text = text.replace("pfx/drive_c/Program Files", "../outside/Program Files")
    if broken == "http": text = text.replace("https://", "http://")
    if broken == "args": text = text.replace('["/S"]', '[123]')
    if broken == "duplicate": text = text.replace('id = "ea"', 'id = "gog"')
    if broken == "id": text = text.replace('id = "ea"', 'id = "../ea"')
    if broken == "empty": text = 'schema_version=1\nversion="2"\nlaunchers=[]'
    sha = "0" * 64 if broken == "hash" else hashlib.sha256(text.encode()).hexdigest()
    write_json(paths.cache / "index.json", {"launchers": {"toml": text, "sha256": sha}})
    assert [p["id"] for p in content.launchers(paths)] == ["epic", "gog", "ea", "ubisoft", "battlenet"]
    assert "using bundled" in caplog.text and "source: bundled" in caplog.text


def test_bundled_catalog_survives_install_payload_and_release(tmp_path, monkeypatch, built_plugin):
    import importlib.util
    import tarfile
    from deckcompanion import install
    root = tmp_path / "repo"
    atomic(root / "content/launchers.toml", "catalog fixture")
    assert "content/launchers.toml" in install.files(root)
    spec = importlib.util.spec_from_file_location("dc_launcher_build", Path(__file__).resolve().parents[1] / "scripts/build_release.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, "ROOT", root)
    result = build.build(tmp_path / "out", offline=True)
    with tarfile.open(result["archive"]) as archive:
        assert archive.extractfile("content/launchers.toml").read() == b"catalog fixture"
