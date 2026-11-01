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
    code = MARKER + f'''# Tested against pinned umu-protonfixes; not verified on hardware.
import importlib
import sys
from pathlib import Path
import protonfixes
from protonfixes import util

def _upstream(stage):
    # Import from the active GE package, never search the localfix directory.
    name = protonfixes.__name__ + '.gamefixes-steam.{appid}'
    source = Path(protonfixes.__file__).parent / 'gamefixes-steam/{appid}.py'
    if not source.is_file():
        print('DeckCompanion: no upstream gamefix for {appid}', file=sys.stderr)
        return
    upstream = importlib.import_module(name)
    hook_with_id = getattr(upstream, stage + '_with_id', None)
    hook = getattr(upstream, stage, None)
    if hook_with_id is not None or hook is not None:
        print('DeckCompanion: upstream {appid} ' + stage, file=sys.stderr)
        if hook_with_id is not None:
            hook_with_id('{appid}')
        else:
            hook()

@util.once(retry=True)
def _deckcompanion_{revision}():
'''
    steps = [f"    if util.protontricks({verb!r}) is False:\n        raise RuntimeError({('protontricks failed: ' + verb)!r})"
             for verb in wine.get("winetricks", [])]
    steps += [f"    util.regedit_add({e['key']!r}, {e['name']!r}, {e['type']!r}, {e['value']!r})" for e in wine.get("registry", [])]
    code += "\n".join(steps or ["    pass"]) + "\n\ndef main():\n"
    code += "    _upstream('main')\n"
    code += f"    print('DeckCompanion: recipe {appid} main', file=sys.stderr)\n"
    code += f"    try:\n        _deckcompanion_{revision}()\n"
    code += f"    except Exception as exc:\n        print('DeckCompanion: recipe {appid} install failed (will retry): ' + str(exc), file=sys.stderr)\n"
    code += "    try:\n        _runtime()\n    except Exception as exc:\n"
    code += f"        print('DeckCompanion: recipe {appid} runtime failed: ' + str(exc), file=sys.stderr)\n\ndef _runtime():\n"
    runtime = []
    for dll, order in wine.get("dll_overrides", {}).items():
        runtime.append(f"    util.winedll_override({dll!r}, util.OverrideOrder({order!r}))")
    for key, value in wine.get("env", {}).items():
        runtime.append(f"    util.set_environment({key!r}, {value!r})")
    code += "\n".join(runtime or ["    pass"]) + "\n"
    code += "\ndef early():\n    _upstream('early')\n"
    if not path.exists() or path.read_text(encoding="utf-8") != code:
        atomic(path, code)
    return path


def remove(paths, appid):
    path = paths.config / "localfixes" / f"{app_id(appid)}.py"
    if path.exists() and path.read_text(encoding="utf-8").startswith(MARKER):
        path.unlink()
