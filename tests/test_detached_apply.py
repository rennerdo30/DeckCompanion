"""Hardware regressions, with fake time/processes and no real Steam/network."""
import importlib
import importlib.util
import json
from pathlib import Path
import tarfile
import pytest
import vdf
from deckcompanion import __version__, cli, install, steam
from deckcompanion.common import Error, atomic, locked, read_json, save_state, state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Tests must mock process launches")
    monkeypatch.setattr(steam.subprocess, "Popen", deny)


class Clock:
    value = 0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def helper_module():
    return importlib.import_module("deckcompanion.apply_helper")


def test_linux_handoff_returns_before_shutdown(paths, monkeypatch, capsys):
    save_state(paths, {"games": {"42": {"pending": "enable"}}})
    monkeypatch.setattr(steam.sys if hasattr(steam, "sys") else __import__("sys"), "platform", "linux")
    monkeypatch.setattr(steam, "running", lambda: True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "gamescope")
    monkeypatch.setattr(steam.shutil, "which", lambda name: "/usr/bin/systemd-run")
    commands = []
    class Process:
        pid = 4242
        returncode = 0

        def communicate(self, timeout):
            assert timeout == 5  # No waiting for Steam to close in the caller.
            return b"", b""

    def spawn(command, **kwargs):
        commands.append(command)
        assert command[0] == "/usr/bin/systemd-run"
        return Process()
    monkeypatch.setattr(steam.subprocess, "Popen", spawn)
    monkeypatch.setattr(steam.time, "sleep", lambda _: pytest.fail("Caller waited for Steam"))
    assert cli.main(["apply", "--restart-steam", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"applied": [], "scheduled": True, "steam_restarted": False, "helper": result["helper"]}
    command, = commands
    assert command[1:5] == ["--user", "--unit=" + result["helper"], "--collect", "--property=RuntimeMaxSec=300"]
    assert command[5] == "--" and command[-2:] == ["apply-helper", "--game-mode"]
    assert result["helper"].startswith("deckcompanion-apply-")
    assert str(paths.data) in command[8].replace("\\\\", "\\")
    assert 4242 in steam.STARTED_PIDS
    assert not steam.mapping_path(paths).exists()


@pytest.fixture
def helper_env(paths, monkeypatch):
    helper = helper_module()
    clock = Clock()
    monkeypatch.setattr(helper.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(helper.time, "sleep", clock.sleep)
    processes = {"steam": True, "gamescope": False, "exit_at": 40}
    calls = []
    def running():
        return processes["steam"] and clock.value < processes["exit_at"]
    monkeypatch.setattr(steam, "running", running)
    monkeypatch.setattr(helper, "gamescope_running", lambda: processes["gamescope"])
    monkeypatch.setattr(helper, "game_mode", lambda: processes["gamescope"])
    monkeypatch.setattr(helper.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(command, timeout=10):
        calls.append((command, timeout, clock.value))
        if command[-1] == "gamescope" or command[-2:] == ["steam", "-silent"]:
            processes.update(steam=True, exit_at=float("inf"), gamescope=command[-1] == "gamescope")
    monkeypatch.setattr(steam, "run", run)
    atomic(steam.mapping_path(paths), vdf.dumps({"keep": "original"}))
    save_state(paths, {"games": {"42": {"pending": "enable"}}})
    return helper, clock, processes, calls


@pytest.mark.parametrize("mode", [False, True])
def test_helper_waits_writes_and_restores(paths, helper_env, mode, monkeypatch, capsys):
    helper, clock, processes, calls = helper_env
    processes["gamescope"] = mode
    real_write = steam.atomic
    writes = []
    def guarded(path, payload):
        if path == steam.mapping_path(paths):
            assert clock.value >= 40 and not steam.running()
            writes.append(path)
        real_write(path, payload)
    monkeypatch.setattr(steam, "atomic", guarded)
    # Exercise the internal CLI, including lock ownership and receipt/exit code.
    assert cli.main(["apply-helper", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] == [{"appid": "42", "action": "enable"}]
    assert result["errors"] == [] and result["steam_restarted"] is True
    assert result["pid"] == helper.os.getpid()
    assert result["game_mode"] is mode
    assert result["timings"]["shutdown"] == pytest.approx(40, abs=.051)
    assert writes == [steam.mapping_path(paths)]
    assert calls[0] == (["steam", "-shutdown"], 120, 0)
    if mode:
        assert calls[1][:2] == (["/usr/bin/steamos-session-select", "gamescope"], 15)
        assert calls[1][2] == pytest.approx(50, abs=.051)
    else:
        assert calls[1][0][0:2] == ["/usr/bin/systemd-run", "--user"]
        assert calls[1][0][-3:] == ["--", "steam", "-silent"]
    assert len(list(steam.mapping_path(paths).parent.glob("*.bak"))) == 1
    assert steam.read_vdf(steam.mapping_path(paths))["keep"] == "original"
    assert state(paths)["games"]["42"]["pending"] is None
    assert read_json(paths.data / "logs/apply-last.json") == result
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["last_apply"] == result


def test_helper_never_exited_writes_nothing(paths, helper_env):
    helper, clock, processes, calls = helper_env
    processes["exit_at"] = float("inf")
    before = steam.mapping_path(paths).read_bytes()
    result = helper.apply_helper(paths, True)
    assert result["errors"][0]["code"] == "steam_shutdown_timeout"
    assert result["applied"] == [] and not result["steam_restarted"]
    assert clock.value == 240 and len(calls) == 2
    assert steam.mapping_path(paths).read_bytes() == before
    assert not list(steam.mapping_path(paths).parent.glob("*.bak"))
    assert state(paths)["games"]["42"]["pending"] == "enable"


def test_helper_lock_prevents_parallel_shutdown(paths, helper_env):
    helper, _, _, calls = helper_env
    atomic(paths.data / "logs/apply-last.json", '{"active":true}')
    with locked(paths, "apply.lock"):
        with pytest.raises(Error, match="Another"):
            helper.apply_helper(paths)
    assert calls == []
    assert read_json(paths.data / "logs/apply-last.json") == {"active": True}


def test_game_mode_already_restored_skips_selector(paths, helper_env, monkeypatch):
    helper, clock, processes, calls = helper_env
    processes["gamescope"] = True
    def auto_restore(seconds):
        clock.sleep(seconds)
        if clock.value >= 42:
            processes["exit_at"] = float("inf")
    monkeypatch.setattr(helper.time, "sleep", auto_restore)
    assert helper.apply_helper(paths, True)["steam_restarted"] is True
    assert len(calls) == 1 and clock.value == pytest.approx(42, abs=.051)


def test_write_failure_still_restores_session(paths, helper_env, monkeypatch):
    helper, _, _, calls = helper_env
    monkeypatch.setattr(steam, "commit_apply", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    result = helper.apply_helper(paths, True)
    assert result["errors"][0]["message"] == "disk full"
    assert result["steam_restarted"] and result["applied"] == []
    assert calls[-1][0][-1] == "gamescope"
    assert state(paths)["games"]["42"]["pending"] == "enable"


def test_restore_selector_alias_and_failed_start(paths, helper_env, monkeypatch):
    helper, _, _, calls = helper_env
    monkeypatch.setattr(helper.shutil, "which", lambda name: "/usr/bin/holo-session-select" if name == "holo-session-select" else None)
    assert helper.apply_helper(paths, True)["steam_restarted"] is True
    assert calls[-1][0][0] == "/usr/bin/holo-session-select"


def test_restore_failure_keeps_applied_receipt(paths, helper_env, monkeypatch):
    helper, _, _, _ = helper_env
    monkeypatch.setattr(steam, "run", lambda *args, **kwargs: None)
    result = helper.apply_helper(paths)
    assert result["applied"] == [{"appid": "42", "action": "enable"}]
    assert not result["steam_restarted"]
    assert result["errors"][0]["code"] == "steam_start_failed"


def test_blocking_shutdown_shares_120_second_budget(paths, helper_env, monkeypatch):
    helper, clock, processes, calls = helper_env
    processes["exit_at"] = float("inf")
    def shutdown(command, timeout):
        calls.append(command)
        clock.sleep(40)
    monkeypatch.setattr(steam, "run", shutdown)
    result = helper.apply_helper(paths)
    assert result["errors"][0]["code"] == "steam_shutdown_timeout"
    assert clock.value == 240 and calls == [["steam", "-shutdown"], ["steam", "-shutdown"]]


def test_busy_state_lock_retries_without_repeating_writes(paths, helper_env, monkeypatch):
    from contextlib import contextmanager
    helper, clock, _, _ = helper_env
    real_lock = helper.locked
    attempts = []
    @contextmanager
    def contend(paths, name="state.lock"):
        attempts.append(name)
        if name == "state.lock" and attempts.count(name) == 1:
            raise Error("busy", "queued preference write")
        with real_lock(paths, name):
            yield
    monkeypatch.setattr(helper, "locked", contend)
    result = helper.apply_helper(paths)
    assert result["errors"] == [] and result["steam_restarted"]
    assert attempts.count("state.lock") == 2
    assert result["timings"]["prepare"] == .25 and result["timings"]["apply"] == 0


def test_fallback_double_forks_sets_session_and_bounds_worker(monkeypatch):
    helper = helper_module()
    calls = []
    monkeypatch.setattr(helper.os, "fork", lambda: calls.append("fork") or 0, raising=False)
    monkeypatch.setattr(helper.os, "setsid", lambda: calls.append("setsid"), raising=False)
    monkeypatch.setattr(helper.os, "chdir", lambda path: calls.append(("chdir", path)))
    monkeypatch.setattr(helper.os, "dup2", lambda *args: None)
    monkeypatch.setattr(helper.os, "sysconf", lambda _: 1024, raising=False)
    monkeypatch.setattr(helper.os, "closerange", lambda *args: calls.append(("close", args)))
    monkeypatch.setattr(steam, "run", lambda command, timeout: calls.append((command, timeout)))
    def leave(code):
        raise SystemExit(code)
    monkeypatch.setattr(helper.os, "_exit", leave)
    with pytest.raises(SystemExit) as exited:
        helper.double_fork(["fake-helper"])
    assert exited.value.code == 0
    assert calls == ["fork", "setsid", "fork", ("chdir", "/"), ("close", (3, 1024)), (["fake-helper"], 300)]


@pytest.mark.parametrize("comm", ["gamescope", "start-gamescope-", "unrelated"])
def test_live_gamescope_process_detection(tmp_path, monkeypatch, comm):
    helper = helper_module()
    atomic(tmp_path / "123/comm", comm)
    monkeypatch.setattr(helper, "Path", lambda _: tmp_path)
    assert helper.gamescope_running() is (comm != "unrelated")


def test_handoff_fallback_only_for_missing_runner(paths, monkeypatch):
    helper = helper_module()
    monkeypatch.setattr(helper, "game_mode", lambda: False)
    monkeypatch.setattr(helper.shutil, "which", lambda _: None)
    calls = []
    monkeypatch.setattr(helper, "double_fork", lambda command: calls.append(command))
    assert helper.schedule(paths)["scheduled"] is True
    assert calls[0][-1] == "apply-helper"
    monkeypatch.setattr(helper.shutil, "which", lambda _: "/usr/bin/systemd-run")
    def fail(*args, **kwargs):
        raise Error("process_failed", "no user manager")
    monkeypatch.setattr(steam, "run", fail)
    with pytest.raises(Error, match="no user manager"):
        helper.schedule(paths)
    assert len(calls) == 1


@pytest.mark.parametrize("desktop,flag", [("gamescope", ""), ("KDE", "SteamDeck"), ("KDE", "SteamGamepadUI"), ("KDE", "")])
def test_detection_environment(monkeypatch, desktop, flag):
    helper = helper_module()
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    for key in ("SteamDeck", "SteamGamepadUI"):
        monkeypatch.delenv(key, raising=False)
    if flag:
        monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(helper, "gamescope_running", lambda: False)
    assert helper.game_mode() is bool(flag or desktop == "gamescope")


def test_install_running_queues_files_and_png_icon(paths, tmp_path, monkeypatch):
    source = tmp_path / "source"
    for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "bin/deckcompanion-ui",
                 "assets/logo/deckcompanion-icon.svg", "assets/logo/deckcompanion-icon-256.png"):
        atomic(source / name, b"mock payload")
    for name in ("compat/DeckCompanion", "compatibilitytool.vdf", "toolmanifest.vdf"):
        atomic(source / name, (ROOT / name).read_bytes())
    if hasattr(install.os, "geteuid"):
        monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(steam, "running", lambda: True)
    save_state(paths, {"games": {"42": {"pending": "enable"}}})
    result = install.install(paths, source)
    assert result["steam_changes_pending"] is True and result["shortcut_pending"] is True
    assert (paths.tools / "DeckCompanion/DeckCompanion").exists()
    assert (paths.data / "bin/deckcompanion-ui").exists()
    assert (paths.data / "current").read_text().strip() == __version__
    assert not steam.mapping_path(paths).exists() and not steam.shortcut_path(paths).exists()
    assert state(paths)["games"]["42"]["pending"] == "enable"
    monkeypatch.setattr(steam, "running", lambda: False)
    steam.apply(paths)
    shortcut = vdf.binary_loads(steam.shortcut_path(paths).read_bytes())["shortcuts"]["0"]
    icon = paths.data / "assets/icon.png"
    assert shortcut["icon"] == str(icon) and icon.read_bytes() == b"mock payload"


@pytest.mark.parametrize("with_payload", [False, True])
def test_release_includes_optional_ui_and_logo(tmp_path, monkeypatch, with_payload):
    spec = importlib.util.spec_from_file_location("dc_build", ROOT / "scripts/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "source"
    source.mkdir()
    if with_payload:
        atomic(source / "bin/deckcompanion-ui", b"mock UI")
        atomic(source / "assets/logo/deckcompanion-icon.svg", "<svg/>")
        atomic(source / "assets/logo/deckcompanion-icon-256.png", b"mock PNG")
    monkeypatch.setattr(builder, "ROOT", source)
    result = builder.build(tmp_path / "out", offline=True)
    with tarfile.open(result["archive"]) as tar:
        names = tar.getnames()
        for name in ("bin/deckcompanion-ui", "assets/logo/deckcompanion-icon.svg", "assets/logo/deckcompanion-icon-256.png"):
            assert (name in names) is with_payload
        if with_payload:
            assert tar.getmember("bin/deckcompanion-ui").mode & 0o111


def test_gamescope_wl_counts_as_game_mode(tmp_path, monkeypatch):
    from deckcompanion import apply_helper
    proc = tmp_path / "proc"
    (proc / "42").mkdir(parents=True)
    (proc / "42" / "comm").write_text("gamescope-wl\n")
    real_path = apply_helper.Path
    monkeypatch.setattr(apply_helper, "Path", lambda p: proc if p == "/proc" else real_path(p))
    assert apply_helper.gamescope_running()
