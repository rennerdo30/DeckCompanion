import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile
import pytest
import vdf
from deckcompanion import cli, net, steam
from deckcompanion.common import Error, atomic, save_state, write_json


@pytest.fixture
def decky(paths, tmp_path, monkeypatch):
    from deckcompanion import decky
    monkeypatch.setattr(decky, "homebrew", lambda: tmp_path / "homebrew")
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(steam, "game_running", lambda ident: False)
    monkeypatch.setattr(decky.sys, "platform", "test")
    return decky


def call(paths, *args):
    return cli.execute(cli.parser().parse_args([*args, "--json"]), paths)


def test_decky_status_missing_offline_and_cli(paths, decky):
    def offline(*a, **kw):
        raise Error("offline", "unavailable")
    from unittest.mock import patch
    with patch.object(decky.net, "fetch", offline):
        result = call(paths, "decky", "status")
    assert result["installed"] is False and result["version"] is None
    assert result["service"] == "unknown"
    assert result["latest"] == {"source": "unavailable", "stale": True, "version": None}


@pytest.mark.parametrize("service", ["active", "inactive", "failed", "unknown"])
def test_installed_version_service_no_root(paths, decky, monkeypatch, service):
    atomic(decky.homebrew() / "services/PluginLoader", "binary")
    atomic(decky.homebrew() / "services/.loader.version", "v3.3.1\n")
    write_json(paths.cache / "decky-release.json", {"version": "v3.3.1", "checked_at": 100})
    monkeypatch.setattr(decky.time, "time", lambda: 200)
    monkeypatch.setattr(decky.sys, "platform", "linux")
    calls = []
    class Proc:
        pid = 987
        def communicate(self, timeout):
            assert timeout == 5
            return service.encode(), None
    monkeypatch.setattr(decky.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd) or Proc())
    result = call(paths, "decky", "status")
    assert result["installed"] and result["version"] == "v3.3.1"
    assert result["service"] == service
    assert result["latest"]["source"] == "cache"
    assert calls == [["systemctl", "is-active", "plugin_loader"]]
    assert 987 in steam.STARTED_PIDS


def test_latest_cache_refresh_then_fallback(paths, decky, monkeypatch):
    calls = []
    def fetch(url, target, **kw):
        calls.append(url)
        atomic(target, json.dumps({"tag_name": "v3.3.1", "prerelease": False}))
    monkeypatch.setattr(net, "fetch", fetch)
    monkeypatch.setattr(decky.time, "time", lambda: 100000)
    assert decky.latest(paths)["source"] == "github"
    assert decky.latest(paths)["source"] == "cache"
    assert calls == [decky.RELEASE_URL]
    def offline(*a, **kw):
        raise Error("offline", "offline")
    monkeypatch.setattr(net, "fetch", offline)
    monkeypatch.setattr(decky.time, "time", lambda: 200000)
    fallback = decky.latest(paths)
    assert fallback["stale"] and fallback["version"] == "v3.3.1"


@pytest.mark.parametrize("payload", [{"tag_name": "v9-pre", "prerelease": True}, {}, []])
def test_bad_latest_keeps_cache(paths, decky, monkeypatch, payload):
    write_json(paths.cache / "decky-release.json", {"version": "v3.3.1", "checked_at": "bad"})
    monkeypatch.setattr(net, "fetch", lambda url, target, **kw: atomic(target, json.dumps(payload)))
    assert decky.latest(paths)["version"] == "v3.3.1"
    assert decky.latest(paths)["stale"]


@pytest.mark.parametrize("desktop,graphical", [(False, False), (False, True), (True, False)])
def test_install_headless_or_gamemode_never_launches(paths, decky, monkeypatch, desktop, graphical):
    from deckcompanion import apply_helper
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setattr(apply_helper, "game_mode", lambda: not desktop)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if graphical:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(steam, "run", lambda *a, **kw: pytest.fail("Must not launch installer"))
    result = call(paths, "decky", "install")
    assert result["needs_desktop"] and not result["launched"]
    assert result["command"] == decky.INSTALL_COMMAND
    assert "sudo" in " ".join(result["instructions"])


@pytest.mark.parametrize("fail", [False, True])
def test_desktop_installer_detached_bounded_and_fallback(paths, decky, monkeypatch, fail):
    from deckcompanion import apply_helper
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setattr(apply_helper, "game_mode", lambda: False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    def run(cmd, timeout):
        calls.append((cmd, timeout))
        if fail:
            raise Error("failed", "no user bus")
    monkeypatch.setattr(steam, "run", run)
    result = call(paths, "decky", "install")
    assert result["launched"] is not fail
    cmd, timeout = calls[0]
    assert timeout == 5 and cmd[0:3] == ["/usr/bin/systemd-run", "--user", "--collect"]
    assert "--property=RuntimeMaxSec=1800" in cmd
    assert cmd[-6:] == ["/usr/bin/konsole", "--separate", "-e", "sh", "-c", decky.INSTALL_COMMAND]
    assert "sudo" not in cmd and "password" not in cmd


def test_panel_current_and_latest_launch_and_empty(paths, decky, monkeypatch):
    assert call(paths, "decky", "panel")["game"] is None
    save_state(paths, {"games": {"42": {"last_launch": {"time": "2026-01-01T00:00:00+00:00", "ok": True}},
                                "43": {"last_launch": {"time": "2026-02-01T00:00:00+00:00", "ok": True}, "proton_override": "GE-Proton11-11"}}})
    result = call(paths, "decky", "panel")
    assert result["game"]["appid"] == "43" and result["selection"] == "last"
    assert result["game"]["proton_override"] == "GE-Proton11-11"
    assert "splash" in result["settings"]
    monkeypatch.setattr(steam, "game_running", lambda ident: ident == "42")
    assert call(paths, "decky", "panel")["selection"] == "running"
    assert call(paths, "decky", "panel")["game"]["appid"] == "42"
    assert call(paths, "decky", "panel", "--appid", "43")["game"]["appid"] == "43"


def test_panel_steam_last_played_and_nonsteam_state(paths, decky, monkeypatch, tmp_path):
    save_state(paths, {"games": {}, "non_steam": {"demo": {"appid": "1234567890", "slug": "demo", "name": "Demo",
        "umu_id": "umu-default", "exe": str(tmp_path / "game.exe"), "last_launch": {"time": "2026-11-01T00:00:00+00:00", "ok": True}}}})
    assert call(paths, "decky", "panel")["game"]["kind"] == "non_steam"
    monkeypatch.setattr(steam, "last_played", lambda paths: {"42": 9999999999})
    assert call(paths, "decky", "panel")["game"]["appid"] == "42"


def test_status_shortcut_is_real_unsigned_appid_not_guess(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    assert call(paths, "status")["shortcut_appid"] is None
    atomic(steam.shortcut_path(paths), steam.shortcut_bytes(paths, steam.shortcut_path(paths)))
    ident = str(steam.shortcut_identity(paths)[1])
    assert call(paths, "status")["shortcut_appid"] == ident
    assert call(paths, "decky", "panel")["shortcut_appid"] == ident
    doc = vdf.binary_loads(steam.shortcut_path(paths).read_bytes())
    doc["shortcuts"]["0"]["Exe"] = "foreign"
    atomic(steam.shortcut_path(paths), vdf.binary_dumps(doc))
    assert call(paths, "status")["shortcut_appid"] is None


def test_cli_decky_command_contract_exists(paths, capsys):
    code = cli.main(["decky", "panel", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["game"] is None
    assert result["shortcut_appid"] is None


def test_desktop_kde_ignores_inherited_hardware_flag(paths, decky, monkeypatch):
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setenv("SteamDeck", "1")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    monkeypatch.setattr(steam, "run", lambda cmd, timeout: calls.append(cmd))
    assert decky.install()["launched"]
    assert len(calls) == 1


def plugin_zip(root, names):
    archive = root / "decky-plugin/dist/DeckCompanion.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in names:
            text = json.dumps({"version": "0.1.0"}) if name == "package.json" else json.dumps({"name": "DeckCompanion"}) if name == "plugin.json" else "payload"
            bundle.writestr("DeckCompanion/" + name, text)
    return archive


def plugin_release_fixture(paths, decky, tmp_path, monkeypatch):
    source = tmp_path / "source"
    archive = plugin_zip(source, decky.PLUGIN_FILES)
    release = {"version": "0.1.0", "sha256": net.digest(archive), "size": archive.stat().st_size,
               "url": "https://github.com/rennerdo30/DeckCompanion/releases/download/decky-v0.1.0/deckcompanion-decky-0.1.0.zip"}
    monkeypatch.setattr(decky, "plugin_release", lambda paths: release)
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **kw: atomic(target, archive.read_bytes()))
    return archive, release


def test_plugin_install_independent_preserves_existing_and_requires_writable(paths, decky, tmp_path, monkeypatch):
    plugin_release_fixture(paths, decky, tmp_path, monkeypatch)
    assert call(paths, "decky", "plugin-install")["reason"] == "decky_missing"
    atomic(decky.homebrew() / "services/PluginLoader", "binary")
    directory = decky.homebrew() / "plugins"
    directory.mkdir()
    monkeypatch.setattr(decky.os, "access", lambda *a: False)
    assert decky.plugin_install(paths)["reason"] == "plugins_not_writable"
    monkeypatch.setattr(decky.os, "access", lambda *a: True)
    assert call(paths, "decky", "plugin-status")["can_install"]
    assert not (directory / "DeckCompanion").exists()
    assert decky.plugin_install(paths)["installed"]
    assert (directory / "DeckCompanion/main.py").read_text() == "payload"
    assert decky.plugin_install(paths)["changed"] is False
    atomic(directory / "DeckCompanion/main.py", "user edited")
    assert decky.plugin_install(paths)["reason"] == "existing_plugin_preserved"
    assert (directory / "DeckCompanion/main.py").read_text() == "user edited"


def test_plugin_archive_rejects_traversal_before_writes(paths, decky, tmp_path, monkeypatch):
    atomic(decky.homebrew() / "services/PluginLoader", "binary")
    (decky.homebrew() / "plugins").mkdir()
    archive, release = plugin_release_fixture(paths, decky, tmp_path, monkeypatch)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("DeckCompanion/../outside", "bad")
    release["sha256"] = net.digest(archive)
    with pytest.raises(Error, match="Unexpected plugin"):
        decky.plugin_install(paths)
    assert not (decky.homebrew() / "plugins/DeckCompanion").exists()
    assert not (decky.homebrew() / "outside").exists()


def test_plugin_release_filters_engine_prereleases_and_uses_digest(paths, decky, monkeypatch):
    def release(version, draft=False, prerelease=False):
        name = f"deckcompanion-decky-{version}.zip"
        return {"tag_name": "decky-v" + version, "draft": draft, "prerelease": prerelease,
                "assets": [{"name": name, "size": 100, "digest": "sha256:" + "a" * 64,
                            "browser_download_url": f"https://github.com/rennerdo30/DeckCompanion/releases/download/decky-v{version}/{name}"}]}
    info = [{"tag_name": "v99.0.0"}, release("9.0.0", prerelease=True), release("8.0.0", draft=True),
            release("0.1.9"), release("0.1.10")]
    def fetch(url, target, *a, **kw):
        assert url.startswith(decky.PLUGIN_RELEASES)
        atomic(target, json.dumps(info))
    monkeypatch.setattr(net, "fetch", fetch)
    result = decky.plugin_release(paths)
    assert result["version"] == "0.1.10" and result["sha256"] == "a" * 64
    assert result["source"] == "github" and not result["stale"]
    assert decky.plugin_release(paths)["source"] == "cache"
    monkeypatch.setattr(decky.time, "time", lambda: result["checked_at"] + decky.CACHE_SECONDS + 1)
    monkeypatch.setattr(net, "fetch", lambda *a, **kw: (_ for _ in ()).throw(Error("offline", "offline")))
    assert decky.plugin_release(paths)["stale"]
    assert decky.plugin_release(paths)["version"] == "0.1.10"


@pytest.mark.parametrize("mode", ["no_release", "bad_digest"])
def test_plugin_unpublished_or_unverified_stays_unavailable(paths, decky, monkeypatch, mode):
    info = [] if mode == "no_release" else [{"tag_name": "decky-v0.1.0", "assets": [{"name": "deckcompanion-decky-0.1.0.zip", "digest": None}]}]
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **kw: atomic(target, json.dumps(info)))
    assert decky.plugin_release(paths)["source"] == "unavailable"


def test_plugin_update_retains_backup_and_rejects_bad_sha(paths, decky, tmp_path, monkeypatch):
    archive, release = plugin_release_fixture(paths, decky, tmp_path, monkeypatch)
    atomic(decky.homebrew() / "services/PluginLoader", "binary")
    (decky.homebrew() / "plugins").mkdir()
    assert decky.plugin_install(paths)["changed"]
    # Different bytes under a later release, without touching engine/current.
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in decky.PLUGIN_FILES:
            text = json.dumps({"version": "0.2.0"}) if name == "package.json" else json.dumps({"name": "DeckCompanion"}) if name == "plugin.json" else "new"
            bundle.writestr("DeckCompanion/" + name, text)
    release.update(version="0.2.0", sha256="0" * 64)
    with pytest.raises(Error, match="SHA256"):
        decky.plugin_install(paths)
    assert (decky.homebrew() / "plugins/DeckCompanion/main.py").read_text() == "payload"
    release["sha256"] = net.digest(archive)
    result = decky.plugin_install(paths)
    assert result["version"] == "0.2.0" and result["changed"]
    assert (Path(result["backup"]) / "main.py").read_text() == "payload"
    assert not (paths.data / "current").exists()


def test_plugin_activation_failure_restores_previous_install(paths, decky, tmp_path, monkeypatch):
    archive, release = plugin_release_fixture(paths, decky, tmp_path, monkeypatch)
    atomic(decky.homebrew() / "services/PluginLoader", "binary")
    (decky.homebrew() / "plugins").mkdir()
    decky.plugin_install(paths)
    before = (paths.data / "decky-plugin-installed.json").read_bytes()
    # A repacked release with the same version is still a byte change and needs verification.
    release["sha256"] = "f" * 64
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.comment = b"repacked"
    release["sha256"] = net.digest(archive)
    monkeypatch.setattr(decky, "write_json", lambda *a: (_ for _ in ()).throw(OSError("receipt denied")))
    with pytest.raises(OSError, match="receipt denied"):
        decky.plugin_install(paths)
    assert (decky.homebrew() / "plugins/DeckCompanion/main.py").read_text() == "payload"
    assert (paths.data / "decky-plugin-installed.json").read_bytes() == before


def load_backend(monkeypatch, tmp_path):
    logger = SimpleNamespace(info=lambda *a: None)
    monkeypatch.setitem(sys.modules, "decky", SimpleNamespace(DECKY_USER_HOME=str(tmp_path), logger=logger))
    path = Path(__file__).resolve().parents[1] / "decky-plugin/main.py"
    spec = importlib.util.spec_from_file_location("dc_plugin", path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def test_backend_allowlist_and_no_shell(monkeypatch, tmp_path):
    backend = load_backend(monkeypatch, tmp_path)
    async def check():
        plugin = backend.Plugin()
        await plugin._main()
        calls = []
        async def command(*args):
            calls.append(args)
            return {}
        monkeypatch.setattr(plugin, "_cli", command)
        await plugin.panel("42")
        await plugin.change("42", "routing", True)
        await plugin.change("42", "proton", "GE-Proton11-11")
        await plugin.change("42", "mod", {"id": "deck-mode", "enabled": False})
        await plugin.change(None, "splash", False)
        assert calls == [("decky", "panel", "--appid", "42"), ("enable", "42"),
            ("game-option", "42", "--proton", "GE-Proton11-11"), ("mod", "42", "deck-mode", "--off"),
            ("settings", "--splash", "off")]
        for args in [("../bad", "routing", True), ("42", "exec", "rm"), ("42", "routing", "false"),
                     ("42", "mod", {"id": "--json", "enabled": True}), ("42", "proton", "--bad")]:
            with pytest.raises(ValueError):
                await plugin.change(*args)
        assert len(calls) == 5
    asyncio.run(check())


@pytest.mark.parametrize("mode", ["ok", "timeout", "oversize", "invalid"])
def test_backend_cli_timeout_output_and_cleanup(monkeypatch, tmp_path, mode):
    backend = load_backend(monkeypatch, tmp_path)
    async def check():
        plugin = backend.Plugin()
        await plugin._main()
        class Proc:
            pid = 1234
            returncode = None
            killed = False
            sent = False
            @property
            def stdout(self):
                return self
            async def read(self, size):
                if self.sent:
                    return b""
                self.sent = True
                if mode == "timeout":
                    raise asyncio.TimeoutError()
                return b"x" * (backend.MAX_OUTPUT + 1) if mode == "oversize" else b"no json" if mode == "invalid" else b'{"game":null}'
            async def wait(self):
                self.returncode = 0
            def kill(self):
                self.killed = True
        proc = Proc()
        calls = []
        async def spawn(*args, **kw):
            calls.append((args, kw))
            return proc
        monkeypatch.setattr(backend.asyncio, "create_subprocess_exec", spawn)
        result = await plugin.panel("42")
        assert calls[0][0] == (str(tmp_path / ".local/share/deckcompanion/bin/deckcompanion"), "decky", "panel", "--appid", "42", "--json")
        assert not plugin.processes
        if mode == "ok":
            assert result == {"game": None} and not proc.killed
        else:
            assert result["error"]["code"] == "cli_unavailable"
            assert proc.killed is (mode != "invalid")
    asyncio.run(check())


def test_install_via_desktop_writes_one_shot_autostart_and_switches(paths, tmp_path, monkeypatch):
    # Owner UX report 2026-11-01: Game Mode must not show raw commands; switch and open the installer.
    from deckcompanion import decky, steam
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    monkeypatch.setattr(steam, "run", lambda command, timeout: calls.append(command) or b"")
    result = decky.install_via_desktop(paths)
    autostart = tmp_path / "config/autostart/deckcompanion-decky-install.desktop"
    script = paths.data / "run/decky-install.sh"
    assert result["switching"] is True and autostart.is_file() and script.is_file()
    assert f"Exec=sh {script}" in autostart.read_text()
    text = script.read_text()
    assert f"rm -f '{autostart}'" in text.splitlines()[1]  # one-shot: removes itself first
    assert decky.INSTALL_COMMAND in text and "konsole" in text
    assert calls and calls[0][-2:] == ["/usr/bin/steamos-session-select", "plasma"]


def test_install_with_password_runs_official_installer_as_root_and_deletes_the_password(paths, tmp_path, monkeypatch):
    # Owner 2026-11-01: install/update Decky inside DeckCompanion, no Desktop Mode switch.
    from deckcompanion import decky, net, steam
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    secret = tmp_path / "pw"
    secret.write_text("hunter2\n", encoding="utf-8")
    runs, restarts = [], []
    def run(command, input=None, **kwargs):
        runs.append((command, input))
        class Result: returncode = 0
        return Result()
    monkeypatch.setattr(decky.subprocess, "run", run)
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **k: target.write_text("#!/bin/sh\n") or target)
    monkeypatch.setattr(steam, "run", lambda command, timeout: restarts.append(command) or b"")
    result = decky.install_with_password(paths, str(secret))
    assert not secret.exists()  # read once, deleted at once
    assert runs[0][0][-1] == "true" and runs[0][1] == "hunter2\n"  # password check first
    assert runs[1][0][:5] == ["/usr/bin/sudo", "-S", "-k", "-p", ""] and runs[1][0][-1].endswith("decky-install_release.sh")
    assert "hunter2" not in (paths.data / "logs/decky-install.log").read_text()
    assert result["steam_restarting"] is True and restarts


def test_install_with_password_reports_a_wrong_password(paths, tmp_path, monkeypatch):
    from deckcompanion import decky
    from deckcompanion.common import Error
    import pytest
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    secret = tmp_path / "pw"
    secret.write_text("wrong", encoding="utf-8")
    class Failed: returncode = 1
    monkeypatch.setattr(decky.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(Error, match="not accepted"):
        decky.install_with_password(paths, str(secret))
    assert not secret.exists()


def test_drive_passwd_answers_prompts_for_an_account_without_password():
    import time
    from deckcompanion import decky
    prompts = iter(["Changing password for deck.\nCurrent password: ", "New password: ", "Retype new password: ", None])
    written = []
    answered = decky.drive_passwd(lambda: next(prompts), written.append, "s3cret", time.monotonic() + 5)
    assert written == ["\n", "s3cret\n", "s3cret\n"] and answered == 2


def test_set_password_refuses_when_a_password_exists(tmp_path, monkeypatch):
    import pytest
    from deckcompanion import decky
    from deckcompanion.common import Error
    secret = tmp_path / "pw"
    secret.write_text("longenough", encoding="utf-8")
    monkeypatch.setattr(decky, "password_set", lambda: True)
    with pytest.raises(Error, match="already has a password"):
        decky.set_password(str(secret))
    assert not secret.exists()
