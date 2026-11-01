"""Offline launch-order, payload and fail-open regressions for DC-18."""
import json
from pathlib import Path
import subprocess
import pytest
from deckcompanion import cli, launch, recipes, steam
from deckcompanion.common import atomic, read_json, save_state


@pytest.fixture
def runtime(paths, monkeypatch):
    proton = paths.tools / "GE-Proton11-11"
    atomic(proton / "proton", "fake Proton")
    monkeypatch.setenv("SteamAppId", "42")
    # prepare receives a plain dict; model Linux's case-sensitive environment on Windows.
    monkeypatch.setattr(launch.os, "environ", dict(launch.os.environ, SteamAppId="42"))
    monkeypatch.setenv("DISPLAY", ":mock")
    monkeypatch.setenv("DECKCOMPANION_UI", "mock-ui")
    monkeypatch.setattr(steam, "installed", lambda p: {"42": {"name": "Fixture Game", "path": str(p.data / "game")}})
    monkeypatch.setattr(recipes, "load", lambda *a: {"version": "1.2.3", "name": "Fixture Game",
        "runtime": {"proton": proton.name, "env": {"DC_FIX": "yes"}, "launch_options": ["--recipe"]},
        "wine": {"winetricks": ["win7"], "dll_overrides": {"dxgi": "n,b"},
                 "registry": [{"key": "HKCU\\Game", "name": "Mode", "type": "REG_SZ", "value": "1"}]},
        "mods": [{"id": "fixture", "name": "Fixture Mod", "version": "1", "mode": "copy",
                  "root": "prefix", "target": "pfx/mod.zip", "sha256": "", "url": "https://example.org/mod"}]})
    atomic(paths.steam / "appcache/librarycache/42/hash/library_hero.jpg", b"art")
    return proton


def command(*args):
    return cli.parser().parse_args(["settings", *args, "--json"])


def test_settings_defaults_clamp_persist_and_preserve(paths):
    save_state(paths, {"games": {"42": {"mods": {"keep": False}}}, "settings": {"future": 9}})
    assert cli.execute(command(), paths) == {"splash": True, "splash_seconds": 4,
        "show_games_without_recipe": True, "check_updates_on_start": True, "future": 9,
        "auto_install_ge": True, "cleanup_old_ge": True, "splash_parallel": False, "takeover_all": True}
    result = cli.execute(command("--splash", "off", "--splash-seconds", "99"), paths)
    assert result["splash"] is False and result["splash_seconds"] == 10
    assert cli.execute(command(), paths)["splash"] is False
    assert cli.execute(command("--splash", "on", "--splash-seconds", "-1"), paths)["splash_seconds"] == 1
    saved = read_json(paths.data / "state.json")
    assert saved["games"]["42"]["mods"] == {"keep": False} and saved["settings"]["future"] == 9


@pytest.mark.parametrize("number", ["nan", "inf", "-inf"])
def test_settings_reject_nonfinite(paths, number):
    cli.execute(command("--splash-seconds", "3"), paths)
    with pytest.raises(Exception):
        cli.execute(command("--splash-seconds=" + number), paths)
    assert cli.execute(command(), paths)["splash_seconds"] == 3


def supervise(monkeypatch, paths, mode="ok", parallel=False):
    # Most tests cover the wait-first mode; parallel mode has its own test below.
    cli.execute(command("--set", "splash_parallel=" + ("on" if parallel else "off")), paths)
    events, payloads, files = [], [], []
    class Process:
        pid = 45123
        def wait(self, timeout):
            events.append(("wait", timeout))
            if mode == "timeout" and timeout != 2:
                raise subprocess.TimeoutExpired("splash", timeout)
            return 7 if mode == "crash" else 0
        def kill(self):
            events.append(("kill", self.pid))
    def popen(args, **kwargs):
        events.append(("splash", args[0]))
        assert args[1] == "--splash"
        files.append(Path(args[2]))
        payloads.append(json.loads(files[-1].read_text(encoding="utf-8")))
        assert kwargs["env"]["DC_FIX"] == "yes"
        if mode == "missing": raise FileNotFoundError("missing UI")
        return Process()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(launch.os, "execve", lambda exe, args, env: events.append(("exec", (exe, args))))
    return events, payloads, files


def test_launch_payload_actual_preparation_and_order(paths, runtime, monkeypatch):
    events, payloads, files = supervise(monkeypatch, paths)
    launch.launch(paths, "run", ["game.exe", "--space argument"])
    assert [e[0] for e in events] == ["splash", "wait", "exec"]
    assert events[1] == ("wait", 126)
    data = payloads[0]
    assert data["name"] == "Fixture Game" and data["appid"] == "42"
    assert data["proton"] == {"name": runtime.name, "reason": "pinned"}
    assert Path(data["art"]["hero"]).is_file() and data["art"]["header"] is None
    assert data["recipe_version"] == "1.2.3" and data["duration_seconds"] == 4
    assert data["fixes"] == {"upstream_chained": True, "wine_steps_count": 3,
                            "wine_steps": ["Winetricks: win7", "Registry: HKCU\\Game / Mode", "DLL: dxgi = n,b"]}
    assert data["mods"][0]["state"] == "will_install_on_launch"
    assert data["mods"][0]["state_message"] and isinstance(data["warnings"], list)
    assert events[-1][1][1] == [str(runtime / "proton"), "run", "game.exe", "--space argument", "--recipe"]
    assert not files[0].exists()
    assert launch.splash.STARTED_PIDS[-1] == 45123


@pytest.mark.parametrize("mode", ["timeout", "crash", "missing"])
def test_splash_failure_still_execs_original_proton(paths, runtime, monkeypatch, mode):
    events, payloads, files = supervise(monkeypatch, paths, mode)
    launch.launch(paths, "waitforexitandrun", ["game.exe"])
    assert events[0][0] == "splash" and events[-1][0] == "exec"
    if mode == "timeout":
        assert ("kill", 45123) in events and ("wait", 2) in events
    else:
        assert not any(e[0] == "kill" for e in events)
    assert "continuing launch" in (paths.data / "logs/42-last.log").read_text()
    assert not files[0].exists()


def test_disabled_and_nonrun_never_spawn(paths, runtime, monkeypatch):
    events, _, _ = supervise(monkeypatch, paths)
    cli.execute(command("--splash", "off"), paths)
    launch.launch(paths, "run", ["game.exe"])
    cli.execute(command("--splash", "on"), paths)
    launch.launch(paths, "getcompatpath", ["game.exe"])
    assert [e[0] for e in events] == ["exec", "exec"]


def test_native_passthrough_precedes_preparation_and_splash(paths, runtime, monkeypatch):
    cli.execute(command("--splash", "on"), paths)
    binary = paths.data / "native"
    atomic(binary, b"\x7fELFmock")
    events = []
    monkeypatch.setattr(launch, "prepare", lambda *a, **k: pytest.fail("native must bypass prepare"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("native must bypass splash"))
    monkeypatch.setattr(launch.os, "execv", lambda *a: events.append(a))
    launch.launch(paths, "run", [str(binary), "--json"])
    launch.launch(paths, "getcompatpath", [str(binary)])
    assert events == [(str(binary), [str(binary), "--json"])]


def test_no_display_is_logged_and_hands_off(paths, runtime, monkeypatch):
    cli.execute(command("--splash", "on"), paths)
    events, _, _ = supervise(monkeypatch, paths)
    monkeypatch.setattr(launch.splash.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    launch.launch(paths, "run", ["game.exe"])
    assert [e[0] for e in events] == ["exec"]
    assert "no display" in (paths.data / "logs/42-last.log").read_text()


def test_blocked_mod_warning_and_unconfigured_proton(paths, runtime, monkeypatch):
    prefix = paths.data / "compatdata/42"
    (prefix / "pfx").mkdir(parents=True)
    monkeypatch.setenv("STEAM_COMPAT_DATA_PATH", str(prefix))
    events, payloads, _ = supervise(monkeypatch, paths)
    launch.launch(paths, "run", ["game.exe"])
    assert payloads[-1]["mods"][0]["state"] == "blocked" and payloads[-1]["warnings"]
    # A foreign localfix cannot be advertised as our configured chain/steps.
    atomic(paths.config / "localfixes/42.py", "# foreign")
    launch.launch(paths, "run", ["game.exe"])
    assert payloads[-1]["fixes"]["upstream_chained"] is False
    assert payloads[-1]["fixes"]["wine_steps_count"] == 0
    monkeypatch.setattr(recipes, "load", lambda *a: None)
    # No recipe env is expected on the following launch.
    monkeypatch.setenv("DC_FIX", "yes")
    launch.launch(paths, "run", ["game.exe"])
    assert payloads[-1]["proton"] == {"name": runtime.name, "reason": "newest"}
    assert payloads[-1]["recipe_version"] is None


def test_custom_duration_default_binary_and_summary_error_fail_open(paths, runtime, monkeypatch):
    cli.execute(command("--splash-seconds", "6"), paths)
    monkeypatch.delenv("DECKCOMPANION_UI")
    events, payloads, _ = supervise(monkeypatch, paths)
    launch.launch(paths, "run", ["game.exe"])
    assert events[0] == ("splash", str(paths.data / "bin/deckcompanion-ui"))
    assert events[1] == ("wait", 128) and payloads[0]["duration_seconds"] == 6
    monkeypatch.setattr(steam, "artwork", lambda *a: (_ for _ in ()).throw(OSError("bad artwork")))
    launch.launch(paths, "run", ["game.exe"])
    assert events[-1][0] == "exec"
    assert "summary unavailable" in (paths.data / "logs/42-last.log").read_text()


def test_parallel_splash_starts_game_immediately(paths, runtime, monkeypatch):
    # Owner 2026-11-01: the game loads in the background while the splash shows.
    events, payloads, files = supervise(monkeypatch, paths, parallel=True)
    seen = {}
    real = subprocess.Popen
    launch.launch(paths, "run", ["game.exe"])
    assert [e[0] for e in events] == ["splash", "exec"]  # never waits for the splash
    assert payloads[0]["parallel"] is True and payloads[0]["name"] == "Fixture Game"
    assert files[0].exists() and files[0].parent.name == "run"  # survives the exec into Proton


def test_splash_first_is_the_default(paths):
    # Parallel mode is opt-in: gamescope showed the game's empty window over the splash.
    assert cli.execute(command(), paths)["splash_parallel"] is False
