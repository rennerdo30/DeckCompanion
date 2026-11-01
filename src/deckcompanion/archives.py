"""Contained tar extraction, with optional launch deadline and no special files."""
import os
from pathlib import Path
import tarfile
from .common import Error, inside


def unpack(source, destination, budget=None):
    destination.mkdir(parents=True, exist_ok=True)
    links = []
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 4 * 1024**3:
            raise Error("unsafe_archive", "Archive exceeds expansion limit")
        for member in members:
            if budget:
                budget.remaining()
            name = member.name.removeprefix("./").rstrip("/")
            if not name:
                continue
            target = inside(destination, name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src, target.open("xb") as dst:
                    while block := src.read(128 * 1024):
                        if budget:
                            budget.remaining()
                        dst.write(block)
                target.chmod(member.mode & 0o777)
            elif member.issym() or member.islnk():
                links.append((member, target))
            else:
                raise Error("unsafe_archive", "Archive contains a special file")
        for member, target in links:
            raw = Path(member.linkname)
            link = (target.parent / raw if member.issym() else destination / raw).resolve()
            if raw.is_absolute() or not link.is_relative_to(destination.resolve()):
                raise Error("unsafe_archive", "Archive link escapes destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                target.symlink_to(member.linkname)
            else:
                os.link(link, target)
