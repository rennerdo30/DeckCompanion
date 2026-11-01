"""Steam rewrites are semantic no-ops; fixtures never access a real Steam tree."""
from pathlib import Path

import pytest
import vdf
from deckcompanion import cli, install, steam
from deckcompanion.common import atomic, save_state, state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rewritten(paths, monkeypatch):
    monkeypatch.setattr(steam, 'running', lambda: False)
    def deny(*args, **kwargs):
        pytest.fail('No process should start for a semantic no-op')
    monkeypatch.setattr(steam.subprocess, 'Popen', deny)
    atomic(paths.data / 'assets/icon.png', b'icon')
    path = steam.shortcut_path(paths)
    original = vdf.binary_loads(steam.shortcut_bytes(paths, path))['shortcuts']['0']
    # Real Steam fields, reordered and indexed alongside an unrelated shortcut.
    entry = dict(reversed(list(original.items())))
    entry.update(LastPlayTime=1730460000, OpenVR=0, AllowOverlay=0, IsHidden=1,
                 AllowDesktopConfig=0, Devkit=0, DevkitGameID='', DevkitOverrideAppID=0,
                 FlatpakAppID='', ShortcutPath='', tags={'0': 'Favorites', '1': 'Tools'})
    foreign = {'appid': 42, 'AppName': 'Player shortcut', 'Exe': '"/opt/player"', 'tags': {}}
    atomic(path, vdf.binary_dumps({'shortcuts': {'4': foreign, '19': entry}}))
    doc = {'Unrelated': 'keep'}
    steam.mappings(doc)['0'] = {'priority': '250', 'config': '', 'name': 'DeckCompanion'}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    return path, entry, foreign


@pytest.mark.parametrize('rewrite', ['flags', 'paths', 'unsigned', 'string_id'])
def test_steam_rewrite_does_not_queue_or_rewrite(paths, rewritten, rewrite):
    path, entry, foreign = rewritten
    if rewrite == 'paths':
        entry.update(Exe=entry['Exe'][1:-1], StartDir=entry['StartDir'][1:-1] + '/',
                     icon='"' + entry['icon'] + '"')
    elif rewrite == 'unsigned':
        entry['appid'] = vdf.UINT_64(entry['appid'] & 0xffffffff)
    elif rewrite == 'string_id':
        entry['appid'] = str(entry['appid'] & 0xffffffff)
    atomic(path, vdf.binary_dumps({'shortcuts': {'4': foreign, '19': entry}}))
    before = path.read_bytes()
    data = state(paths)
    data.update(shortcut_pending=True, default_pending='default_on')
    steam.prune_pending(paths, data)
    assert not steam.has_pending(data)
    assert steam.shortcut_bytes(paths, path) == before
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('AppName', 'Old name'), ('StartDir', '"/old/bin"'),
                                      ('icon', '/old/icon.png'), ('LaunchOptions', '--old'),
                                      ('Exe', '"/old/ui"'), ('appid', 17)])
def test_owned_change_queues_and_preserves_player_fields(paths, rewritten, field, value):
    path, entry, foreign = rewritten
    entry[field] = value
    atomic(path, vdf.binary_dumps({'shortcuts': {'4': foreign, '19': entry}}))
    data = state(paths)
    data['shortcut_pending'] = True
    steam.prune_pending(paths, data)
    assert data['shortcut_pending']
    updated = list(vdf.binary_loads(steam.shortcut_bytes(paths, path))['shortcuts'].values())
    assert len(updated) == 2
    assert updated[0] == foreign
    assert updated[1]['AppName'] == 'DeckCompanion'
    assert updated[1]['AllowOverlay'] == 0 and updated[1]['IsHidden'] == 1
    assert updated[1]['tags'] == entry['tags']
    assert updated[1]['LastPlayTime'] == entry['LastPlayTime']


def test_engine_only_update_after_steam_rewrite_needs_no_restart(paths, rewritten, tmp_path, monkeypatch, capsys):
    path, _, _ = rewritten
    source = tmp_path / 'release'
    for name in ('src/deckcompanion/__main__.py', 'vdf/__init__.py', 'bin/deckcompanion-ui'):
        atomic(source / name, b'updated engine/UI payload')
    for name in ('compat/DeckCompanion', 'compatibilitytool.vdf', 'toolmanifest.vdf'):
        atomic(source / name, (ROOT / name).read_bytes())
    for name in steam.ARTWORK:
        atomic(source / f'assets/steam/{name}.png', name.encode())
    atomic(source / 'assets/steam/icon.png', b'icon')
    if hasattr(install.os, 'geteuid'):
        monkeypatch.setattr(install.os, 'geteuid', lambda: 1000)
    ident = steam.shortcut_identity(paths)[1]
    for name, suffix in steam.ARTWORK.items():
        atomic(path.parent / 'grid' / f'{ident}{suffix}.png',
               (source / f'assets/steam/{name}.png').read_bytes())
    before = path.read_bytes()
    assert install.install(paths, source)['steam_changes_pending'] is False
    assert not steam.has_pending(state(paths))
    assert path.read_bytes() == before
    monkeypatch.setattr(steam, 'running', lambda: True)
    assert cli.main(['apply', '--restart-steam', '--json']) == 0
    assert '"steam_restarted": false' in capsys.readouterr().out


def test_mapping_compares_managed_values_and_keeps_steam_extras(paths, rewritten):
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc)['0'].update(priority='0250', SteamExtra='keep')
    steam.mappings(doc)['480'] = dict(steam.mappings(doc)['0'])
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    data = state(paths)
    data.update(default_pending='default_on', games={'480': {'pending': 'enable'}})
    steam.prune_pending(paths, data)
    assert not steam.has_pending(data)
    assert data['games']['480']['enabled']
    assert steam.read_vdf(steam.mapping_path(paths)) == doc


def test_changed_artwork_content_still_queues(paths, rewritten, tmp_path, monkeypatch):
    from deckcompanion import update
    path, _, _ = rewritten
    monkeypatch.setattr(update, 'current', lambda p: 'test')
    atomic(paths.data / 'versions/test/assets/steam/hero.png', b'new hero')
    atomic(path.parent / 'grid' / f'{steam.shortcut_identity(paths)[1]}_hero.png', b'old hero')
    data = state(paths)
    data['shortcut_pending'] = True
    steam.prune_pending(paths, data)
    assert data['shortcut_pending']
