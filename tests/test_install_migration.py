import configparser
import json
from pathlib import Path
import runpy
import shlex
import sys
import subprocess
import pytest
import vdf
from deckcompanion import __version__, cli, content, install, launch, localfix, net, recipes, steam, update
from deckcompanion.common import Error, atomic, read_json, save_state, state, write_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "source"
    for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "recipes/example.txt"):
        atomic(source / name, "# fake payload\n")
    for name in ("compat/DeckCompanion", "compatibilitytool.vdf", "toolmanifest.vdf"):
        atomic(source / name, (ROOT / name).read_bytes())
    monkeypatch.setattr(steam, "running", lambda: False)
    if hasattr(install.os, "geteuid"):
        monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    return source


def test_idempotent_install_queue_ui_and_uninstall_preserves_foreign_files(paths, source):
    atomic(source / "bin/deckcompanion-ui", b"fake UI binary")
    assert install.install(paths, source) == {"installed": True, "version": __version__, "shortcut_pending": True,
        "restart_required": True, "legacy_tool_preserved": False, "steam_changes_pending": True}
    assert update.current(paths) == __version__
    cli_file = paths.data / "bin/deckcompanion"
    assert 'main(sys.argv[1:])' in cli_file.read_text()
    assert 'main(["launch"' not in cli_file.read_text()
    assert install.install(paths, source)["installed"] is True
    assert state(paths)["shortcut_pending"] is True
    steam.apply(paths)
    shortcut = steam.shortcut_path(paths)
    assert vdf.binary_loads(shortcut.read_bytes())["shortcuts"]["0"]["AppName"] == "DeckCompanion"
    doc = {"preserve": "setting"}
    prior = {"name": "proton_10"}
    steam.mappings(doc)["42"] = prior
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    data = state(paths)
    data["games"]["42"] = {"pending": "enable"}
    save_state(paths, data)
    steam.apply(paths)
    marked = localfix.generate(paths, "42", {})
    foreign = paths.config / "localfixes/43.py"
    atomic(foreign, "# foreign\n")
    other = paths.tools / "DeckCompanion/foreign.txt"
    atomic(other, "user file")
    cli_file.write_text("# user modification\n")
    result = install.uninstall(paths)
    assert result["uninstalled"] is True
    assert str(cli_file) in result["preserved"]
    assert cli_file.read_text() == "# user modification\n"
    assert not marked.exists() and foreign.exists() and other.exists()
    assert not (paths.tools / "DeckCompanion/DeckCompanion").exists()
    assert steam.read_vdf(steam.mapping_path(paths)) == doc
    assert vdf.binary_loads(shortcut.read_bytes())["shortcuts"] == {}
    assert not (paths.data / "current").exists()
    assert (paths.data / "state.json").exists()
    assert not (paths.data / "versions" / __version__).exists()


def test_legacy_clone_preserved_install_running_and_uninstall_refused(paths, source, monkeypatch):
    compat = paths.tools / "DeckCompanion"
    atomic(compat / ".git", "gitdir: original")
    atomic(compat / "DeckCompanion", "old migration entry")
    assert install.install(paths, source)["legacy_tool_preserved"] is True
    assert (compat / "DeckCompanion").read_text() == "old migration entry"
    monkeypatch.setattr(steam, "running", lambda: True)
    assert install.install(paths, source)["steam_changes_pending"] is True
    with pytest.raises(Error, match="Close Steam"):
        install.uninstall(paths)


def test_foreign_bin_conflict_and_version_immutability(paths, source):
    atomic(paths.data / "bin/deckcompanion", "user entry")
    with pytest.raises(Error, match="Preserving"):
        install.install(paths, source)
    assert (paths.data / "bin/deckcompanion").read_text() == "user entry"
    atomic(source / "src/deckcompanion/__main__.py", "changed")
    with pytest.raises(Error, match="differs"):
        install.install(paths, source)


def test_uninstall_unchanged_mods_preserves_changed_mods(paths, source, tmp_path):
    install.install(paths, source)
    root = tmp_path / "prefix"
    unchanged = root / "pfx/mods/unchanged.zip"
    changed = root / "pfx/mods/changed.zip"
    atomic(unchanged, b"unchanged")
    atomic(changed, b"original")
    record = {"root_path": str(root), "version": "1", "files": [{"path": str(unchanged), "sha256": net.digest(unchanged)},
              {"path": str(changed), "sha256": net.digest(changed)}]}
    write_json(paths.data / "manifests/42.json", {"mods": {"mod": record}})
    changed.write_bytes(b"user edit")
    result = install.uninstall(paths)
    assert not unchanged.exists() and changed.read_bytes() == b"user edit"
    assert result["preserved"] == [str(changed)]
    assert len(read_json(paths.data / "manifests/42.json")["mods"]["mod"]["files"]) == 1


def shim():
    return runpy.run_path(str(ROOT / "DeckCompanion"))["main"]


def test_migration_shim_fallback_passes_arguments(paths, monkeypatch):
    atomic(paths.tools / "GE-Proton11-11/proton", "fake")
    monkeypatch.setenv("SteamAppId", "42")
    calls = []
    monkeypatch.setattr(steam, "proton", lambda p: [{"name": "GE-Proton11-11", "path": str(paths.tools / "GE-Proton11-11")}])
    monkeypatch.setattr(install.os, "execve", lambda exe, args, env: calls.append((exe, args, env)))
    args = ["waitforexitandrun", "game with spaces.exe", "--json", "$literal", "semi;colon"]
    assert shim()(args) == 0
    assert calls[0][1] == [str(paths.tools / "GE-Proton11-11/proton"), *args]
    assert calls[0][2].get("SteamAppId", calls[0][2].get("STEAMAPPID")) == "42"
    assert "run the new install.sh" in (paths.data / "logs/42-last.log").read_text()


def test_migration_shim_installed_engine_argument_passing(paths, monkeypatch):
    atomic(paths.data / "current", "0.2.0\n")
    atomic(paths.data / "versions/0.2.0/src/deckcompanion/__main__.py", "# fake")
    calls = []
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    args = ["run", "game.exe", "arg with spaces", "--json"]
    assert shim()(args) == 0
    assert calls == [["launch", *args]]


def test_install_uninstall_cli_and_desktop_contract(paths, source, capsys):
    assert cli.main(["install", "--source", str(source), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["shortcut_pending"] is False
    assert cli.main(["uninstall", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["uninstalled"] is True
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "DeckCompanion-Installer.desktop")
    command = shlex.split(parser["Desktop Entry"]["Exec"])
    assert command[:7] == ["konsole", "--hold", "-e", "bash", "-o", "pipefail", "-c"] and len(command) == 8
    assert '--connect-timeout 10 --max-time 30' in command[7]
    assert 'releases/latest/download/install.sh | timeout 300s sh' in command[7]
    assert 'git pull' not in (ROOT / "DeckCompanion").read_text().replace('never runs git pull', '')


def test_restart_leaves_successfully_started_steam_alive(paths, monkeypatch):
    spawned = []
    class Process:
        pid = 87654
        killed = False
        returncode = None
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("steam", timeout)
        def kill(self):
            self.killed = True
        def poll(self):
            return None
        def wait(self, timeout):
            return -1
    proc = Process()
    def create(*args, **kwargs):
        spawned.append(True)
        return proc
    monkeypatch.setattr(steam, "running", lambda: bool(spawned))
    monkeypatch.setattr(steam.subprocess, "Popen", create)
    assert steam.apply(paths, restart=True)["steam_restarted"] is True
    assert proc.killed is False


def test_symlink_rejection_is_actionable_in_game_json(paths, tmp_path, monkeypatch, capsys):
    atomic(paths.tools / "GE-Proton11-11/proton", "fake")
    prefix = tmp_path / "prefix"
    documents = prefix / "pfx/drive_c/users/steamuser/Documents"
    documents.parent.mkdir(parents=True)
    outside = tmp_path / "external-documents"
    outside.mkdir()
    try:
        documents.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    recipe = recipes.load(paths, "2300320")
    # Exercise launch with the real FS25 target, without any network or actual Proton.
    launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "2300320", "STEAM_COMPAT_DATA_PATH": str(prefix)})
    assert cli.main(["game", "2300320", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    for text in (result["notes"], "\n".join(result["log_tail"])):
        assert "fs25-deck-mode" in text
        assert recipe["mods"][0]["target"] in text
        assert str(outside) in text and "symlink redirects outside the root are not allowed" in text
    assert not list(outside.iterdir())
