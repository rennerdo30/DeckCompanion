import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import pytest
from deckcompanion import cli, net, recipes, update
from deckcompanion import __version__
from deckcompanion.common import Error, atomic, read_json, write_json


def engine_archive(paths, version):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "compat/DeckCompanion"):
            info = tarfile.TarInfo(name)
            data = b"# fake engine " + version.encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    sha = hashlib.sha256(stream.getvalue()).hexdigest()
    source = paths.cache / "downloads" / sha
    atomic(source, stream.getvalue())
    return source, sha


def index(paths, version):
    source, sha = engine_archive(paths, version)
    text = 'schema_version = 1\nappid = "42"\nname = "Cached game"\nversion = "1"\n'
    return {"schema_version": 1, "engine": {"version": version, "url": "https://example.org/engine.tar.gz", "sha256": sha},
            "recipes": {"42": {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}}}


def test_atomic_switch_keeps_previous_and_rollback(paths):
    first, sha1 = engine_archive(paths, "0.1")
    update.install_engine(paths, first, "0.1", sha1)
    assert update.current(paths) == "0.1"
    second, sha2 = engine_archive(paths, "0.2")
    update.install_engine(paths, second, "0.2", sha2)
    assert update.current(paths) == "0.2"
    assert (paths.data / "previous").read_text().strip() == "0.1"
    assert (paths.data / "versions/0.1/src/deckcompanion/__main__.py").exists()
    assert update.rollback(paths) == {"version": "0.1", "rolled_back": True}
    assert (paths.data / "previous").read_text().strip() == "0.2"
    assert not list((paths.data / "versions").glob(".install-*"))
    entries = read_json(paths.data / "updates.json", {}).get("entries", [])
    assert [(e["module"], e["from"], e["to"]) for e in entries] == [
        ("engine", __version__, "0.1"), ("engine", "0.1", "0.2")]


def test_invalid_archive_or_digest_never_switches(paths):
    source, sha = engine_archive(paths, "old")
    update.install_engine(paths, source, "old", sha)
    with pytest.raises(Error, match="checksum mismatch"):
        update.install_engine(paths, source, "new", "0"*64)
    bad = paths.cache / "bad.tar"
    with tarfile.open(bad, "w") as tar:
        tar.addfile(tarfile.TarInfo("empty"), io.BytesIO())
    with pytest.raises(Error, match="lacks"):
        update.install_engine(paths, bad, "new", net.digest(bad))
    assert update.current(paths) == "old"
    assert not (paths.data / "versions/new").exists()
    assert not list((paths.data / "versions").glob(".install-*"))


def test_switch_failure_keeps_current_and_existing_version_is_preserved(paths, monkeypatch):
    one, sha1 = engine_archive(paths, "one")
    update.install_engine(paths, one, "one", sha1)
    two, sha2 = engine_archive(paths, "two")
    real_atomic = update.atomic
    def fail_current(path, payload):
        if path.name == "current":
            raise OSError("pointer write failed")
        real_atomic(path, payload)
    monkeypatch.setattr(update, "atomic", fail_current)
    with pytest.raises(OSError):
        update.install_engine(paths, two, "two", sha2)
    assert update.current(paths) == "one"
    assert (paths.data / "versions/two/.release.json").exists()
    monkeypatch.setattr(update, "atomic", real_atomic)
    update.install_engine(paths, two, "two", sha2)
    assert update.current(paths) == "two"
    with pytest.raises(Error, match="differs"):
        update.install_engine(paths, one, "two", sha1)


def test_online_mock_then_offline_cache_and_cli_shape(paths, monkeypatch, capsys):
    candidate = index(paths, "0.3.0")
    monkeypatch.setenv("DECKCOMPANION_INDEX_URL", "https://example.org/index.json")
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **k: write_json(target, candidate))
    assert cli.main(["update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["engine"] == {"current": "0.3.0", "available": "0.3.0", "updated": True}
    assert result["index"]["updated"] is True and result["index"]["fetched_at"]
    assert recipes.load(paths, "42")["name"] == "Cached game"
    assert "42" in recipes.ids(paths)
    def timeout(*a, **kw):
        raise Error("network_timeout", "fake offline")
    monkeypatch.setattr(net, "fetch", timeout)
    result2 = update.update(paths)
    assert result2["engine"] == {"current": "0.3.0", "available": "0.3.0", "updated": False}
    assert result2["index"] == {"updated": False, "fetched_at": result["index"]["fetched_at"]}


def test_invalid_index_cannot_replace_cached_recipes(paths, monkeypatch):
    cached = index(paths, "0.3.0")
    write_json(paths.cache / "index.json", cached)
    bad = index(paths, "0.4.0")
    bad["recipes"]["42"]["toml"] += "# modified"
    monkeypatch.setenv("DECKCOMPANION_INDEX_URL", "https://example.org/index.json")
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **k: write_json(target, bad))
    result = update.update(paths)
    assert result["index"]["updated"] is False
    assert read_json(paths.cache / "index.json") == cached
    assert result["engine"]["current"] == "0.3.0"


def test_first_install_of_running_source_version_creates_pointer(paths, monkeypatch):
    candidate = index(paths, __version__)
    monkeypatch.setenv("DECKCOMPANION_INDEX_URL", "https://example.org/index")
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **kw: write_json(target, candidate))
    assert update.update(paths)["engine"]["updated"] is True
    assert (paths.data / "current").read_text().strip() == __version__


def test_release_api_mock_and_initial_offline(paths, monkeypatch):
    # The runtime installer has its own download/integrity tests; isolate engine URLs.
    monkeypatch.setattr("deckcompanion.umu.install", lambda paths: {"installed": True})
    candidate = index(paths, "0.3.0")
    monkeypatch.delenv("DECKCOMPANION_INDEX_URL", raising=False)
    urls = []
    def fetch(url, target, *a, **kw):
        urls.append(url)
        write_json(target, [{"tag_name": "v0.3.0", "assets": [{"name": "index.json", "browser_download_url": "https://example.org/index"}]}] if len(urls) == 1 else candidate)
    monkeypatch.setattr(net, "fetch", fetch)
    assert update.update(paths)["index"]["updated"] is True
    entries = read_json(paths.data / "updates.json", {}).get("entries", [])
    assert any(e["module"] == "engine" and e["to"] == "0.3.0" for e in entries)
    from deckcompanion.proton import API
    assert urls[:2] == [update.RELEASE_API + "?per_page=100&page=1", "https://example.org/index"]
    assert any("?per_page=100&page=1" in url for url in urls)
    assert urls[-1] == API + "/latest"


def builder_module():
    path = Path(__file__).resolve().parents[1] / "scripts/build_release.py"
    spec = importlib.util.spec_from_file_location("dc_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_build_bundles_fallback_without_resolving_upstream(tmp_path, built_plugin):
    build = builder_module()
    result = build.build(tmp_path / "release")
    index_data = read_json(Path(result["index"]))
    update.validate_index(index_data)
    assert index_data["recipes"] == {}
    with tarfile.open(result["archive"]) as tar:
        names = tar.getnames()
        assert "src/deckcompanion/__main__.py" in names and "vdf/LICENSE" in names
        assert tar.getmember("compat/DeckCompanion").mode & 0o111
        assert not any("__pycache__" in n or n.startswith("ui/") for n in names)
        bundled = __import__("tomllib").loads(tar.extractfile("recipes/2300320.toml").read().decode())
        assert bundled["mods"][0]["version"] == "latest"
        assert bundled["mods"][0]["sha256"] == ""
        assert "sha256" not in bundled["runtime"]
    assert (tmp_path / "release/checksums.sha256").read_text().startswith(result["sha256"])
