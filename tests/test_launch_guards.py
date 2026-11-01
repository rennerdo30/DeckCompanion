from pathlib import Path
import io
import tarfile
import pytest
from deckcompanion import archives, content, localfix, net, recipes, steam
from deckcompanion.common import Error, atomic, save_state


def test_disable_removes_only_marked_localfix_and_rolls_back_on_failure(paths, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    marked = localfix.generate(paths, "42", {})
    foreign = paths.config / "localfixes/43.py"
    atomic(foreign, "# foreign")
    save_state(paths, {"games": {"42": {"enabled": True, "pending": "disable"}, "43": {"pending": "disable"}}})
    original = steam.save_state
    monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        steam.apply(paths)
    assert marked.read_text().startswith(localfix.MARKER)
    assert foreign.read_text() == "# foreign"
    monkeypatch.setattr(steam, "save_state", original)
    steam.apply(paths)
    assert not marked.exists()
    assert foreign.exists()


def test_exhausted_shared_budget_skips_network(paths, monkeypatch):
    ticks = iter([1, 4])
    monkeypatch.setattr(net.time, "monotonic", lambda: next(ticks))
    budget = net.Budget()
    with pytest.raises(Error, match="budget exhausted"):
        budget.remaining()


def test_archive_symlink_escape_rejected(tmp_path):
    source = tmp_path / "link.tar"
    with tarfile.open(source, "w") as out:
        entry = tarfile.TarInfo("escape")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "../../outside"
        out.addfile(entry)
    with pytest.raises(Error, match="escapes"):
        archives.unpack(source, tmp_path / "unpack")


def test_root_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    from deckcompanion.common import inside
    with pytest.raises(Error, match="escapes"):
        inside(root, "link/mod.zip")
