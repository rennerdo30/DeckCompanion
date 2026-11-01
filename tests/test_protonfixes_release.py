import importlib.util
from pathlib import Path
import tarfile


def test_release_excludes_upstream_and_nested_dependencies(tmp_path, monkeypatch, built_plugin):
    spec = importlib.util.spec_from_file_location(
        "dc_protonfixes_build", Path(__file__).resolve().parents[1] / "scripts/build_release.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    root = tmp_path / "repo"
    (root / "recipes").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs/keep.md").write_text("release docs", encoding="utf-8")
    # Exercise both the existing root allowlist and the recursive archive filter.
    for prefix in ("third_party", "docs/third_party", "assets/steam/third_party"):
        upstream = root / prefix / "umu-protonfixes"
        for name in ("fix.py", "util.py", "subprojects/umu-database/COPYING",
                     "subprojects/winetricks/COPYING"):
            path = upstream / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("development only", encoding="utf-8")
    monkeypatch.setattr(build, "ROOT", root)
    result = build.build(tmp_path / "out", offline=True)
    with tarfile.open(result["archive"]) as archive:
        names = archive.getnames()
        assert "docs/keep.md" in names
        assert not any("third_party" in Path(name).parts for name in names)
        assert not any("umu-protonfixes" in name or "subprojects" in name for name in names)
