"""Read-only, record-streamed Steam appinfo (0x28/0x29), with no VDF dependency."""
import os
import struct
from .common import read_json, write_json


class Invalid(ValueError):
    pass


class Reader:
    def __init__(self, stream, end):
        self.stream, self.end = stream, end

    def read(self, size):
        if size < 0 or self.stream.tell() + size > self.end:
            raise Invalid("Truncated appinfo record")
        value = self.stream.read(size)
        if len(value) != size:
            raise Invalid("Truncated appinfo file")
        return value

    def number(self, fmt="<I"):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self, wide=False):
        unit, end = (2, b"\0\0") if wide else (1, b"\0")
        value = bytearray()
        while len(value) < 1024 * 1024:
            part = self.read(unit)
            if part == end:
                return value.decode("utf-16-le" if wide else "utf-8", errors="replace")
            value.extend(part)
        raise Invalid("Oversized appinfo string")


def keyvalues(reader, keys=None, depth=0):
    if depth > 64:
        raise Invalid("Appinfo nesting limit")
    result = {}
    while True:
        kind = reader.number("<B")
        if kind in (8, 11):
            return result
        key = reader.string() if keys is None else keys[reader.number()]
        if kind == 0:
            value = keyvalues(reader, keys, depth + 1)
        elif kind in (1, 5):
            value = reader.string(wide=kind == 5)
        elif kind in (2, 3, 4, 6, 7, 10):
            value = reader.number({2: "<i", 3: "<f", 4: "<I", 6: "<I", 7: "<Q", 10: "<q"}[kind])
        else:
            # Unknown types have no safe length. The caller skips this entire record.
            raise Invalid(f"Unknown appinfo value type {kind}")
        result[key.lower()] = value


def extract(doc):
    app = doc.get("appinfo", doc)
    common = app.get("common", {})
    config = common.get("steam_deck_compatibility", {}).get("configuration", {})
    if not isinstance(config, dict):
        config = {}
    runtime = config.get("recommended_runtime")
    return {"oslist": common.get("oslist", ""), "type": common.get("type", ""),
            "configuration": config, "recommended_runtime": runtime if isinstance(runtime, str) else None,
            "depots": {k: v.get("config", {}).get("oslist", "")
                       for k, v in app.get("depots", {}).items() if k.isdigit() and isinstance(v, dict)}}


def parse(path, log=lambda message: None):
    result = {"apps": {}, "app_mappings": {}, "compat_tools": {}}
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        reader = Reader(stream, size)
        magic = reader.number()
        if magic not in (0x07564428, 0x07564429):
            raise Invalid(f"Unsupported appinfo magic {magic:#x}")
        reader.read(4)  # Universe
        keys = None
        records_end = size
        if magic == 0x07564429:
            records_end = reader.number("<Q")
            if not 20 <= records_end <= size - 4:
                raise Invalid("Invalid appinfo string-table offset")
            stream.seek(records_end)
            count = reader.number()
            if count > min(2_000_000, size - stream.tell()):
                raise Invalid("Invalid appinfo string-table count")
            keys = [reader.string() for _ in range(count)]
            stream.seek(16)
        reader.end = records_end
        while stream.tell() < records_end:
            ident = reader.number()
            if ident == 0:
                break
            length = reader.number()
            end = stream.tell() + length
            if length < 60 or end > records_end:
                raise Invalid("Invalid appinfo record length")
            reader.read(60)  # state, timestamp, token, text SHA1, change number, binary SHA1
            try:
                doc = keyvalues(Reader(stream, end), keys)
                result["apps"][str(ident)] = extract(doc)
                if ident == 891390:
                    extended = doc.get("appinfo", doc).get("extended", {})
                    result.update({k: extended[k] for k in ("app_mappings", "compat_tools")
                                   if isinstance(extended.get(k), dict)})
            except (ValueError, IndexError, KeyError, TypeError, AttributeError) as exc:
                log(f"Appinfo app {ident} skipped: {exc}")
            stream.seek(end)
    return result


_CACHE = {}


def load(paths, log=lambda message: None):
    path = paths.steam / "appcache/appinfo.vdf"
    try:
        stat = path.stat()
        stamp = [str(path.resolve()), stat.st_mtime_ns, stat.st_size]
        key = tuple(stamp)
        if key in _CACHE:
            return _CACHE[key]
        cache = paths.cache / "appinfo.json"
        try:
            saved = read_json(cache, {})
        except (OSError, ValueError):
            saved = {}
        if saved.get("stamp") == stamp and saved.get("schema") == 1:
            result = saved["data"]
        else:
            result = parse(path, log)
            after = path.stat()
            if [str(path.resolve()), after.st_mtime_ns, after.st_size] != stamp:
                raise Invalid("Appinfo changed while reading; retry next command")
            try:
                write_json(cache, {"schema": 1, "stamp": stamp, "data": result})
            except OSError as exc:
                log(f"Appinfo cache unavailable: {exc}")
        _CACHE.clear()
        _CACHE[key] = result
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log(f"Valve metadata unavailable: {exc}")
        return {"apps": {}, "app_mappings": {}, "compat_tools": {}}


def recommendation(metadata, ident):
    profile = metadata.get("apps", {}).get(str(ident), {}).get("recommended_runtime")
    override = metadata.get("app_mappings", {}).get(str(ident), {})
    value = profile or (override.get("tool") if isinstance(override, dict) else None)
    return value if isinstance(value, str) else None
