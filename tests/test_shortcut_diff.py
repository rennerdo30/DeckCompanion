"""Steam-shaped binary fixtures, isolated installs and a mocked apply worker."""
import json
from pathlib import Path

import pytest
import vdf

from deckcompanion import apply_helper, cli, install, steam
from deckcompanion.common import atomic, save_state, state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(steam, 'running', lambda: False)
    monkeypatch.setattr(steam.subprocess, 'Popen', lambda *a, **k: pytest.fail('No real process'))
    if hasattr(install.os, 'geteuid'):
        monkeypatch.setattr(install.os, 'geteuid', lambda: 1000)
    source = tmp_path / 'release'
    for name in ('src/deckcompanion/__main__.py', 'vdf/__init__.py', 'bin/deckcompanion-ui'):
        atomic(source / name, b'fixture payload')
    for name in ('compat/DeckCompanion', 'compatibilitytool.vdf', 'toolmanifest.vdf'):
        atomic(source / name, (ROOT / name).read_bytes())
    for name in steam.ARTWORK:
        atomic(source / f'assets/steam/{name}.png', name.encode())
    return source


def steam_rewrite(paths, *, casing=False, dropped=False, old_icon=False):
    """Steam uses appname/exe, int32 ids, optional empties, flags and arbitrary indices.

    Construct the record independently of shortcut_bytes: generating our own record
    here hid the case mismatch in the previous regression fixture.
    """
    path = steam.shortcut_path(paths)
    unsigned = steam.shortcut_identity(paths)[1]
    icon = paths.data / 'assets/icon.png'
    if old_icon:
        icon = paths.data / 'versions/0.2.2/assets/logo/deckcompanion-icon-256.png'
        atomic(icon, (paths.data / 'assets/icon.png').read_bytes())
    entry = {
        'appid': unsigned - 2**32,
        'appname' if casing else 'AppName': 'DeckCompanion',
        'exe' if casing else 'Exe': str(paths.data / 'bin/deckcompanion-ui'),
        'StartDir': '"' + str(paths.data / 'bin') + '/"',
        'icon': '"' + str(icon) + '"',
        'ShortcutPath': '', 'LaunchOptions': '', 'IsHidden': 0,
        'AllowDesktopConfig': 1, 'AllowOverlay': 1, 'OpenVR': 0, 'Devkit': 0,
        'DevkitGameID': '', 'DevkitOverrideAppID': 0, 'LastPlayTime': 1793530000,
        'FlatpakAppID': '', 'sortas': '', 'tags': {'0': 'Favorites', '1': 'Tools'},
    }
    if dropped:
        entry = {k: v for k, v in entry.items() if v != ''}
    foreign = {'appid': 42, 'appname': 'User app', 'exe': '"/opt/user-app"', 'tags': {}}
    atomic(path, vdf.binary_dumps({'shortcuts': {'7': foreign, '31': entry}}))
    return path, entry, foreign


@pytest.mark.parametrize('rewrite', ['casing', 'dropped', 'old_icon', 'combined'])
def test_second_install_after_realistic_steam_rewrite_queues_nothing(paths, release, monkeypatch, capsys, rewrite):
    monkeypatch.setattr(install, '__version__', '0.4.2')
    assert install.install(paths, release)['steam_changes_pending']
    steam.apply(paths)
    path, _, _ = steam_rewrite(paths, **({k: True for k in ('casing', 'dropped', 'old_icon')}
                                       if rewrite == 'combined' else {rewrite: True}))
    before = path.read_bytes()
    grid = {p: p.read_bytes() for p in path.parent.glob('grid/*.png')}
    monkeypatch.setattr(install, '__version__', '0.4.3')
    assert cli.main(['install', '--source', str(release), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['steam_changes_pending'] is False
    assert result['steam_change_reasons'] == []
    assert not result['shortcut_pending'] and not result['restart_required']
    assert path.read_bytes() == before and all(p.read_bytes() == b for p, b in grid.items())
    monkeypatch.setattr(steam, 'running', lambda: True)
    assert cli.main(['apply', '--restart-steam', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['steam_restarted'] is False


@pytest.mark.parametrize('field,value', [('AppName', 'Changed title'), ('Exe', '/old/ui'),
    ('StartDir', '/old/bin'), ('LaunchOptions', '--debug'), ('icon', '/missing/icon.png'),
    ('icon', 'versioned-content'), ('appid', 17)])
def test_install_names_the_changed_field_and_repairs_without_duplicate_keys(paths, release, field, value):
    install.install(paths, release)
    steam.apply(paths)
    path, entry, foreign = steam_rewrite(paths, casing=True, dropped=True)
    if value == 'versioned-content':
        value = str(paths.data / 'versions/0.2.2/assets/steam/icon.png')
        atomic(Path(value), b'old icon pixels')
    key = {'AppName': 'appname', 'Exe': 'exe'}.get(field, field)
    entry[key] = value
    atomic(path, vdf.binary_dumps({'shortcuts': {'7': foreign, '31': entry}}))
    result = install.install(paths, release)
    assert result['steam_changes_pending']
    reasons = result['steam_change_reasons']
    assert [(r['scope'], r['field']) for r in reasons] == [('shortcut', field)]
    assert reasons[0]['before'] == value
    steam.apply(paths)
    entries = list(vdf.binary_loads(path.read_bytes())['shortcuts'].values())
    assert len(entries) == 2 and entries[0] == foreign
    repaired = entries[1]
    assert len({k.lower() for k in repaired}) == len(repaired)
    assert repaired['tags'] == entry['tags'] and repaired['LastPlayTime'] == entry['LastPlayTime']
    assert not install.install(paths, release)['steam_changes_pending']


@pytest.mark.parametrize('name', list(steam.ARTWORK))
def test_artwork_reason_is_a_content_hash_not_a_version_path(paths, release, name):
    install.install(paths, release)
    steam.apply(paths)
    path, _, _ = steam_rewrite(paths, casing=True, dropped=True, old_icon=True)
    dest = path.parent / 'grid' / f'{steam.shortcut_identity(paths)[1]}{steam.ARTWORK[name]}.png'
    atomic(dest, b'outdated picture')
    result = install.install(paths, release)
    reasons = result['steam_change_reasons']
    assert [(r['scope'], r['field']) for r in reasons] == [('artwork', name)]
    assert len(reasons[0]['before']) == 64 and len(reasons[0]['after']) == 64
    assert reasons[0]['before'] != reasons[0]['after']
    steam.apply(paths)
    assert dest.read_bytes() == (release / f'assets/steam/{name}.png').read_bytes()
    assert not install.install(paths, release)['steam_changes_pending']


@pytest.mark.parametrize('changed', [True, False])
def test_apply_receipt_explains_the_shortcut_write(paths, release, monkeypatch, changed):
    install.install(paths, release)
    steam.apply(paths)
    path, entry, foreign = steam_rewrite(paths, casing=True, dropped=True)
    if changed:
        entry['LaunchOptions'] = '--old'
    atomic(path, vdf.binary_dumps({'shortcuts': {'7': foreign, '31': entry}}))
    data = state(paths)
    data['shortcut_pending'] = True
    save_state(paths, data)
    monkeypatch.setattr(apply_helper, 'game_mode', lambda: False)
    running = {'steam': True}
    monkeypatch.setattr(steam, 'running', lambda: running['steam'])
    def shutdown(command, timeout):
        assert command == ['steam', '-shutdown']
        receipt = json.loads((paths.data / 'logs/apply-last.json').read_text())
        assert receipt['steam_change_reasons'][0]['field'] == 'LaunchOptions'
        running['steam'] = False
    monkeypatch.setattr(steam, 'run', shutdown)
    monkeypatch.setattr(apply_helper, 'restore', lambda mode: running.update(steam=True))
    result = apply_helper.apply_helper(paths)
    assert result['verified'] and not result['errors']
    assert result['steam_restarted'] is changed
    assert [(r['scope'], r['field']) for r in result['steam_change_reasons']] == ([('shortcut', 'LaunchOptions')] if changed else [])
    if changed:
        assert result['steam_change_reasons'][0]['before'] == '--old'
    assert json.loads((paths.data / 'logs/apply-last.json').read_text())['steam_change_reasons'] == result['steam_change_reasons']
