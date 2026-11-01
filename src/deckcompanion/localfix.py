"""Generate only marked localfixes, preserving the running GE's upstream game fix."""
import hashlib
from .common import Error, atomic, app_id

MARKER = "# managed-by: DeckCompanion\n"


def generate(paths, appid, recipe):
    appid = app_id(appid)
    path = paths.config / "localfixes" / f"{appid}.py"
    if path.exists() and not path.read_text(encoding="utf-8").startswith(MARKER):
        raise Error("foreign_localfix", f"Preserving user localfix: {path}")
    wine = recipe.get("wine", {})
    revision = hashlib.sha256(repr(wine).encode()).hexdigest()[:16]
    code = MARKER + f'''# Upstream chaining requires verification on hardware.
import importlib
from pathlib import Path
import protonfixes
from protonfixes import util

def _upstream():
    # Import from the active GE package, never search the localfix directory.
    name = protonfixes.__name__ + '.gamefixes-steam.{appid}'
    source = Path(protonfixes.__file__).parent / 'gamefixes-steam/{appid}.py'
    if not source.is_file():
        return None
    return importlib.import_module(name)

@util.once(retry=True)
def _deckcompanion_{revision}():
'''
    steps = [f"    util.protontricks({verb!r})" for verb in wine.get("winetricks", [])]
    steps += [f"    util.regedit_add({e['key']!r}, {e['name']!r}, {e['type']!r}, {e['value']!r})" for e in wine.get("registry", [])]
    code += "\n".join(steps or ["    pass"]) + "\n\ndef main():\n"
    code += "    upstream = _upstream()\n    if upstream is not None and hasattr(upstream, 'main'):\n        upstream.main()\n"
    code += f"    _deckcompanion_{revision}()\n"
    for dll, order in wine.get("dll_overrides", {}).items():
        code += f"    util.winedll_override({dll!r}, util.OverrideOrder({order!r}))\n"
    for key, value in wine.get("env", {}).items():
        code += f"    util.set_environment({key!r}, {value!r})\n"
    code += "\ndef early():\n    upstream = _upstream()\n    if upstream is not None and hasattr(upstream, 'early'):\n        upstream.early()\n"
    if not path.exists() or path.read_text(encoding="utf-8") != code:
        atomic(path, code)
    return path


def remove(paths, appid):
    path = paths.config / "localfixes" / f"{app_id(appid)}.py"
    if path.exists() and path.read_text(encoding="utf-8").startswith(MARKER):
        path.unlink()
