"""Install migrates only obsolete files bearing our ownership marker."""
import pytest
import vdf

from deckcompanion import appinfo, install, localfix, steam
from deckcompanion.common import atomic, save_state, state
from test_shortcut_diff import release  # isolated payload, processes and Steam tree


@pytest.mark.parametrize('obsolete', ['ui', 'umu', 'elf', 'script', 'native_steam', 'orphan', 'crlf_orphan'])
def test_install_prunes_obsolete_managed_localfix_and_preserves_foreign(paths, release, monkeypatch, obsolete):
    ident = str(steam.shortcut_identity(paths)[1]) if obsolete == 'ui' else '41'
    data = state(paths)
    data.setdefault('settings', {})['takeover_all'] = False
    # Native and owned shortcuts must be cleaned even with enabled state/recipes.
    if obsolete not in ('orphan', 'crlf_orphan'):
        data['games'][ident] = {'enabled': True}
        atomic(release / f'recipes/{ident}.toml',
               f'schema_version = 1\nappid = "{ident}"\nname = "Native"\nversion = "1"\n')
    if obsolete == 'umu':
        data['non_steam'] = {'game': {'slug': 'game', 'appid': ident, 'exe': '/games/game.exe', 'umu_id': 'umu-test'}}
    elif obsolete in ('elf', 'script'):
        exe = paths.data / 'native'
        atomic(exe, b'\x7fELFfixture' if obsolete == 'elf' else b'#!/bin/sh\nexit 0\n')
        atomic(steam.shortcut_path(paths), vdf.binary_dumps({'shortcuts': {'19': {
            'appid': int(ident), 'appname': 'Native', 'exe': '"' + str(exe) + '"'}}}))
    elif obsolete == 'native_steam':
        atomic(paths.steam / f'steamapps/appmanifest_{ident}.acf', vdf.dumps({'AppState': {
            'appid': ident, 'installdir': 'native', 'InstalledDepots': {'141': {'manifest': '1'}}}}))
        monkeypatch.setattr(appinfo, 'load', lambda p: {'apps': {ident: {
            'oslist': 'windows,linux', 'depots': {'141': 'linux'}}}})
    # Keep enabled/no-recipe, disabled/recipe, pending-enable, and foreign content.
    data['games'].update({'51': {'enabled': True}, '53': {'pending': 'enable'}})
    atomic(release / 'recipes/52.toml', 'schema_version=1\nappid="52"\nname="Game"\nversion="1"\n')
    kept = {'51': localfix.MARKER.encode() + b'keep enabled',
            '52': localfix.MARKER.encode() + b'keep recipe',
            '53': localfix.MARKER.encode() + b'keep pending enable',
            '61': b'# user-owned\n' + localfix.MARKER.encode(), '62': b'\xff\x00user-owned'}
    for appid, payload in kept.items():
        atomic(paths.config / f'localfixes/{appid}.py', payload)
    obsolete_path = paths.config / f'localfixes/{ident}.py'
    marker = localfix.MARKER.replace('\n', '\r\n') if obsolete == 'crlf_orphan' else localfix.MARKER
    atomic(obsolete_path, marker.encode() + b'obsolete')
    save_state(paths, data)
    install.install(paths, release)
    assert all((paths.config / f'localfixes/{appid}.py').read_bytes() == payload for appid, payload in kept.items())
    assert not obsolete_path.exists()
