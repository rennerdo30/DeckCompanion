"""Exercise the pinned loader/util, not a reimplementation or a Proton process."""
import importlib
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import types
import urllib.request

import pytest

from deckcompanion import localfix, recipes
from deckcompanion.common import Error


UPSTREAM = Path(__file__).resolve().parents[1] / "third_party/umu-protonfixes"
pytestmark = pytest.mark.skipif(
    not (UPSTREAM / "fix.py").is_file() or not (UPSTREAM / "util.py").is_file(),
    reason="Initialize third_party/umu-protonfixes (non-recursively) for upstream tests",
)


@pytest.fixture
def upstream(paths, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(home)
    # Windows expanduser uses USERPROFILE; Linux uses HOME. Isolate both.
    for key, value in {"HOME": home, "USERPROFILE": home,
                       "XDG_CONFIG_HOME": home / ".config",
                       "XDG_CACHE_HOME": home / ".cache",
                       "STEAM_COMPAT_DATA_PATH": home / "compatdata/2300320",
                       "SteamAppId": "2300320", "PWD": home,
                       "PROTONFIXES_DISABLE": "0", "PROTONFIXES_DEBUG": "1"}.items():
        monkeypatch.setenv(key, str(value))
    for key in ("UMU_ID", "STORE", "SteamGameId", "PROTON_DLL_COPY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", [str(home / "proton"), "waitforexitandrun", "game.exe"])
    monkeypatch.setattr(sys, "path", list(sys.path))  # loader appends to this list

    def deny(*args, **kwargs):
        raise AssertionError("Upstream sandbox forbids processes and network")

    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(os, "system", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    prefixes = ("protonfixes", "localfixes")
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in prefixes}
    for name in saved:
        del sys.modules[name]
    logs, calls = [], []
    package = types.ModuleType("protonfixes")
    package.__file__ = str(UPSTREAM / "__init__.py")
    package.__path__ = [str(UPSTREAM)]
    # Avoid __init__.py's automatic early execution, GUI and Proton imports.
    sys.modules["protonfixes"] = package
    logger = types.ModuleType("protonfixes.logger")
    logger.LogLevel = types.SimpleNamespace(INFO="info")
    logger.log = types.SimpleNamespace(**{name: lambda message: logs.append(message)
                                         for name in ("log", "info", "warn", "crit", "debug")})
    sys.modules[logger.__name__] = logger
    try:
        util = importlib.import_module("protonfixes.util")
        fix = importlib.import_module("protonfixes.fix")
        fix.config.main.enable_checks = False  # /proc checks are Linux/host-specific
        for name in ("protontricks", "regedit_add", "set_environment", "winedll_override", "replace_command"):
            monkeypatch.setattr(util, name, lambda *args, _name=name: calls.append((_name, *args)))
        yield types.SimpleNamespace(fix=fix, util=util, calls=calls, logs=logs, home=home,
                                    paths=replace(paths, config=home / ".config/protonfixes"))
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in prefixes:
                del sys.modules[name]
        sys.modules.update(saved)


def recipe_for(sandbox, appid):
    # FS25 currently has no Wine steps; overlay representative steps without changing it.
    recipe = recipes.load(sandbox.paths, appid) if appid == "2300320" else {}
    recipe["wine"] = {"winetricks": ["dc-recipe"],
                      "registry": [{"key": "HKCU\\DC", "name": "n", "type": "REG_SZ", "value": "v"}],
                      "dll_overrides": {"dc-dll": "n,b"}, "env": {"DC_TEST": "yes"}}
    return recipe


def prepare(sandbox, monkeypatch, appid):
    monkeypatch.setenv("SteamAppId", appid)
    # A real upstream main_with_id fix only redirects when the extender exists.
    (sandbox.home / "obse_loader.exe").touch()
    # Real bundled default.py consumes this option before the per-game fix.
    sys.argv.append("-pf_tricks=dc-default")
    return localfix.generate(sandbox.paths, appid, recipe_for(sandbox, appid))


def expected_upstream(appid, util):
    if appid == "244210":
        return [("protontricks", "dotnet48"), ("protontricks", "d3dx11_43"),
                ("protontricks", "d3dcompiler_47"),
                ("winedll_override", "dwrite", util.OverrideOrder.NATIVE_BUILTIN),
                ("protontricks", "win10"), ("set_environment", "PULSE_LATENCY_MSEC", "60")]
    if appid == "22330":
        return [("replace_command", "OblivionLauncher.exe", "obse_loader.exe")]
    return []  # FS25 has global defaults, but no per-game module at this pin.


def test_shipped_fs25_recipe_loads_with_no_wine_steps(upstream, capsys):
    appid = "2300320"
    recipe = recipes.load(upstream.paths, appid)
    file = localfix.generate(upstream.paths, appid, recipe)
    sys.argv.append("-pf_tricks=dc-default")
    upstream.fix.run_fix(appid, stage="early")
    upstream.fix.run_fix(appid, stage="main")
    assert Path(sys.modules[f"localfixes.{appid}"].__file__) == file
    assert upstream.calls == [("protontricks", "dc-default")]
    assert "DeckCompanion: recipe 2300320 main" in capsys.readouterr().err


@pytest.mark.parametrize("appid", ["2300320", "244210", "22330"])
def test_real_loader_discovers_localfix_and_chains_hooks(upstream, monkeypatch, capsys, appid):
    file = prepare(upstream, monkeypatch, appid)
    upstream.fix.run_fix(appid, stage="early")
    if appid == "244210":
        assert os.environ["PROTON_DLL_COPY"] == "*"  # real upstream early() ran
    upstream.fix.run_fix(appid, stage="main")
    module = sys.modules[f"localfixes.{appid}"]
    assert Path(module.__file__) == file
    assert any(f"main stage local protonfix" in line for line in upstream.logs)
    bundled = expected_upstream(appid, upstream.util)
    install = [("protontricks", "dc-recipe"), ("regedit_add", "HKCU\\DC", "n", "REG_SZ", "v")]
    runtime = [("winedll_override", "dc-dll", upstream.util.OverrideOrder.NATIVE_BUILTIN),
               ("set_environment", "DC_TEST", "yes")]
    assert upstream.calls == [("protontricks", "dc-default")] + bundled + install + runtime
    # Keep the real once decorator: installs are stamped, runtime settings repeat.
    upstream.calls.clear()
    upstream.fix.run_fix(appid, stage="main")
    assert upstream.calls == bundled + runtime
    stamps = list(upstream.util.protonprefix().glob("drive_c/protonfixes/run/localfixes.*"))
    assert len(stamps) == 1 and stamps[0].name.startswith(f"localfixes.{appid}._deckcompanion_")
    stderr = capsys.readouterr().err
    assert f"DeckCompanion: recipe {appid} main" in stderr
    if bundled:
        assert stderr.index(f"DeckCompanion: upstream {appid} main") < stderr.index(f"DeckCompanion: recipe {appid} main")
    else:
        assert "DeckCompanion: no upstream gamefix for 2300320" in stderr
    # Recipe changes get a new once stamp; foreign localfixes remain untouched.
    recipe = recipe_for(upstream, appid)
    recipe["wine"]["winetricks"] = ["dc-revision"]
    localfix.generate(upstream.paths, appid, recipe)
    del sys.modules[f"localfixes.{appid}"]
    upstream.calls.clear()
    upstream.fix.run_fix(appid, stage="main")
    assert ("protontricks", "dc-revision") in upstream.calls
    localfix.remove(upstream.paths, appid)
    file.write_text("# user localfix\n", encoding="utf-8")
    with pytest.raises(Error, match="Preserving user localfix"):
        localfix.generate(upstream.paths, appid, recipe)
    localfix.remove(upstream.paths, appid)
    assert file.read_text() == "# user localfix\n"


@pytest.mark.parametrize("appid", ["2300320", "244210", "22330"])
@pytest.mark.parametrize("failure", ["exception", "false"])
def test_recipe_failure_keeps_upstream_and_retries_without_stamp(upstream, monkeypatch, capsys, appid, failure):
    prepare(upstream, monkeypatch, appid)
    record = upstream.util.protontricks
    def failing(verb):
        record(verb)
        if verb == "dc-recipe":
            if failure == "false":
                return False  # real protontricks also reports failures this way
            raise RuntimeError("recipe failed deliberately")
    monkeypatch.setattr(upstream.util, "protontricks", failing)
    for _ in range(2):
        upstream.fix.run_fix(appid, stage="main")
    bundled = expected_upstream(appid, upstream.util)
    assert upstream.calls[:1 + len(bundled)] == [("protontricks", "dc-default")] + bundled
    assert upstream.calls.count(("protontricks", "dc-recipe")) == 2
    assert upstream.calls.count(("set_environment", "DC_TEST", "yes")) == 2
    assert not list(upstream.util.protonprefix().glob("drive_c/protonfixes/run/localfixes.*"))
    assert "install failed (will retry)" in capsys.readouterr().err


def test_id_hooks_take_precedence_in_both_stages(upstream, monkeypatch):
    appid = "22330"
    prepare(upstream, monkeypatch, appid)
    bundled = importlib.import_module(f"protonfixes.gamefixes-steam.{appid}")
    monkeypatch.setattr(bundled, "early_with_id", lambda game: upstream.calls.append(("early-id", game)), raising=False)
    monkeypatch.setattr(bundled, "early", lambda: pytest.fail("early_with_id takes precedence"), raising=False)
    monkeypatch.setattr(bundled, "main", lambda: pytest.fail("main_with_id takes precedence"), raising=False)
    upstream.fix.run_fix(appid, stage="early")
    upstream.fix.run_fix(appid, stage="main")
    assert upstream.calls[:3] == [("early-id", appid), ("protontricks", "dc-default"),
                                 ("replace_command", "OblivionLauncher.exe", "obse_loader.exe")]
