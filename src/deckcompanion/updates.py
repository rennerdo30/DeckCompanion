"""Bounded notification state, independent of launch/settings state writers."""
from .common import locked, now, read_json, write_json


def record(paths, module, name, previous, version):
    if not version or previous == version:
        return
    with locked(paths, "updates.lock"):
        data = read_json(paths.data / "updates.json", {"entries": []})
        data["entries"] = (data["entries"] + [{"module": module, "name": name,
                            "from": previous, "to": version, "at": now(), "seen": False}])[-50:]
        write_json(paths.data / "updates.json", data)


def summary(paths, seen=False):
    from . import decky
    with locked(paths, "updates.lock"):
        data = read_json(paths.data / "updates.json", {"entries": []})
        changed = [{k: v for k, v in entry.items() if k != "seen"}
                   for entry in data["entries"] if not entry.get("seen")]
        if seen:
            for entry in data["entries"]:
                entry["seen"] = True
            write_json(paths.data / "updates.json", data)
    return {"changed": changed, "available": decky.available(paths)}
