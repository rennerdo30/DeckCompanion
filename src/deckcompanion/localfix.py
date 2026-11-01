"""Generate only marked localfixes, preserving the running GE's upstream game fix."""
import hashlib
from pathlib import Path
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
    if managed(path):
        path.unlink()


def managed(path):
    return path.is_file() and path.read_bytes().startswith((MARKER.encode(), MARKER.replace('\n', '\r\n').encode()))


def prune(paths, data):
    """Migrate obsolete managed fixes; foreign files are never decoded or changed."""
    import re
    from . import appinfo, launch, recipes, steam
    import vdf
    obsolete = {str(steam.shortcut_identity(paths)[1])}
    obsolete.update(str(g['appid']) for g in data.get('non_steam', {}).values())
    for path in (paths.steam / 'userdata').glob('*/config/shortcuts.vdf'):
        for entry in vdf.binary_loads(path.read_bytes()).get('shortcuts', {}).values():
            ident = steam.shortcut_value('appid', steam.shortcut_field(entry, 'appid'))
            exe = steam.shortcut_value('Exe', steam.shortcut_field(entry, 'Exe'))
            if ident is not None and (steam.shortcut_owned(paths, entry) or launch.native([exe])):
                obsolete.add(str(ident))
    metadata = appinfo.load(paths)
    platforms = lambda value: set(re.split(r'[,\s]+', str(value).lower())) - {''}
    for ident, entry in steam.installed(paths).items():
        manifest = steam.read_vdf(Path(entry['library']) /
                                 f'steamapps/appmanifest_{ident}.acf').get('AppState', {})
        app = metadata['apps'].get(ident, {})
        chosen = manifest.get('UserConfig', {}).get('platform_override_dest') or manifest.get('oslist')
        systems = platforms(chosen) if chosen else set().union(*(
            platforms(app.get('depots', {}).get(d, '')) for d in manifest.get('InstalledDepots', {})))
        if not systems:
            systems = platforms(app.get('oslist', ''))
        if 'linux' in systems and 'windows' not in systems:
            obsolete.add(ident)
    for file in (paths.config / 'localfixes').glob('[0-9]*.py'):
        if not file.stem.isdigit() or not managed(file):
            continue
        saved = data['games'].get(file.stem, {})
        enabled = saved.get('enabled') or saved.get('pending') == 'enable'
        if file.stem in obsolete or (not enabled and recipes.load(paths, file.stem) is None):
            remove(paths, file.stem)
