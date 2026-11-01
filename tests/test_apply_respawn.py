"""Timed fake process-table regressions for SteamOS gamescope auto-respawn."""
from pathlib import Path

import pytest
import vdf
from deckcompanion import apply_helper as helper, steam
from deckcompanion.common import Error, atomic, read_json, save_state, state


class Processes:
    def __init__(self, windows, overwrite=lambda: None):
        self.time = 0.0
        self.windows = windows
        self.shutdowns = 0
        self.exit_at = float('inf')
        self.overwrite = overwrite
        self.rewritten = False
        self.samples = []

    def sleep(self, seconds):
        self.time += seconds

    def table(self):
        if self.shutdowns and self.time >= self.exit_at:
            if self.time < self.exit_at + self.windows[self.shutdowns - 1]:
                self.samples.append((self.time, False))
                return {}  # Both the steam and steamwebhelper PIDs have exited.
            if not self.rewritten:
                self.overwrite()
                self.rewritten = True
        self.samples.append((self.time, True))
        return {100 + self.shutdowns: 'steam', 200 + self.shutdowns: 'steamwebhelper'}

    def shutdown(self, command, timeout=10):
        assert command == ['steam', '-shutdown'] and timeout == 120
        self.shutdowns += 1
        assert self.shutdowns <= 2
        self.exit_at = self.time + 3.17


@pytest.fixture
def pending(paths, monkeypatch):
    path = steam.shortcut_path(paths)
    atomic(path, vdf.binary_dumps({'shortcuts': {}}))
    atomic(steam.mapping_path(paths), vdf.dumps({'unrelated': 'keep'}))
    atomic(paths.data / 'current', 'test')
    for name in steam.ARTWORK:
        atomic(paths.data / f'versions/test/assets/steam/{name}.png', name.encode())
    save_state(paths, {'games': {'42': {'pending': 'enable'}}, 'default_pending': 'default_on',
                       'shortcut_pending': True})
    def no_process(*args, **kwargs):
        pytest.fail('Fake process table only; never launch a process')
    monkeypatch.setattr(steam.subprocess, 'Popen', no_process)
    return {p: p.read_bytes() for p in [path, steam.mapping_path(paths)]}


def process_table(monkeypatch, table):
    monkeypatch.setattr(helper.time, 'monotonic', lambda: table.time)
    monkeypatch.setattr(helper.time, 'sleep', table.sleep)
    monkeypatch.setattr(helper, 'game_mode', lambda: True)
    monkeypatch.setattr(helper, 'gamescope_running', lambda: True)
    monkeypatch.setattr(steam, 'running', lambda: bool(table.table()))
    monkeypatch.setattr(steam, 'run', table.shutdown)
    real_atomic = steam.atomic
    def timed_write(path, payload):
        real_atomic(path, payload)
        table.sleep(.01)  # Seven queued files take 70 ms, with no process queries.
    monkeypatch.setattr(steam, 'atomic', timed_write)
    def slow_guard():
        table.sleep(.25)
        if steam.running(): raise Error('steam_running', 'slow re-check missed closed window')
    monkeypatch.setattr(steam, 'require_closed', slow_guard)


def test_missed_window_retries_once_and_verifies_all_entries(paths, pending, monkeypatch):
    table = Processes([.01, .2])
    process_table(monkeypatch, table)
    result = helper.apply_helper(paths, True)
    assert result['errors'] == []
    assert result.get('attempts') == 2 and result.get('verified') is True
    assert {c['action'] for c in result['applied']} == {'default_on', 'enable', 'shortcut'}
    assert not steam.has_pending(state(paths))
    assert steam.mapping_matches(steam.mappings(steam.read_vdf(steam.mapping_path(paths)))['42'])
    assert steam.shortcut_bytes(paths, steam.shortcut_path(paths)) == steam.shortcut_path(paths).read_bytes()
    assert all(p.read_bytes() == payload for p, payload in steam.shortcut_artwork(paths, steam.shortcut_path(paths)))
    # No guard/process-table query may consume the short write interval.
    closed_samples = [t for t, running in table.samples if not running]
    assert len(closed_samples) >= 1
    assert read_json(paths.data / 'logs/apply-last.json') == result


def test_persistent_missed_windows_leave_no_partial_writes_and_clear_error(paths, pending, monkeypatch):
    table = Processes([.01, .01])
    process_table(monkeypatch, table)
    before = state(paths)
    result = helper.apply_helper(paths, True)
    assert result.get('attempts') == 2 and result.get('verified') is False
    assert result['errors'][0]['code'] == 'steam_shutdown_timeout'
    assert 'two shutdown cycles' in result['errors'][0]['message']
    assert result['applied'] == [] and state(paths) == before
    assert all(p.read_bytes() == payload for p, payload in pending.items())
    assert not list(steam.shortcut_path(paths).parent.glob('grid/*.png'))
    assert not list(paths.steam.rglob('.dc-backup-*'))
    assert read_json(paths.data / 'logs/apply-last.json') == result


@pytest.mark.parametrize('persistent', [False, True])
def test_steam_overwrite_after_respawn_is_verified_and_retried(paths, pending, monkeypatch, persistent):
    def overwrite():
        if persistent or table.shutdowns == 1:
            for path, payload in pending.items(): atomic(path, payload)
    table = Processes([.2, .2], overwrite)
    process_table(monkeypatch, table)
    before = state(paths)
    # Rewrite on each startup, without treating a normal running-table poll as another startup.
    real_shutdown = table.shutdown
    def shutdown(*args, **kwargs):
        table.rewritten = False
        real_shutdown(*args, **kwargs)
    monkeypatch.setattr(steam, 'run', shutdown)
    result = helper.apply_helper(paths, True)
    assert result.get('attempts') == 2
    assert result.get('verified') is (not persistent)
    if persistent:
        assert result['errors'][0]['code'] == 'steam_apply_verification_failed'
        assert result['applied'] == [] and state(paths) == before
        assert all(p.read_bytes() == payload for p, payload in pending.items())
    else:
        assert result['errors'] == [] and not steam.has_pending(state(paths))


def test_fast_transaction_rolls_back_every_written_file_on_io_failure(paths, pending, monkeypatch):
    table = Processes([.2, .2])
    process_table(monkeypatch, table)
    write = steam.atomic
    broken = False
    def fail_second(path, payload):
        nonlocal broken
        if path == steam.shortcut_path(paths) and not broken:
            broken = True
            raise OSError('disk full')
        write(path, payload)
    monkeypatch.setattr(steam, 'atomic', fail_second)
    before = state(paths)
    result = helper.apply_helper(paths, True)
    assert result['errors'][0]['message'] == 'disk full'
    assert result['applied'] == [] and state(paths) == before
    assert all(p.read_bytes() == payload for p, payload in pending.items())
    assert not list(steam.shortcut_path(paths).parent.glob('grid/*.png'))


def test_retry_retains_original_mapping_restoration_metadata(paths, pending, monkeypatch):
    original = {'name': 'proton_11', 'config': 'player setting', 'priority': '100'}
    doc = steam.read_vdf(steam.mapping_path(paths))
    steam.mappings(doc).update({'0': original, '42': original})
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    def overwrite_shortcut():
        if table.shutdowns == 1:
            path = steam.shortcut_path(paths)
            atomic(path, pending[path])
    table = Processes([.2, .2], overwrite_shortcut)
    process_table(monkeypatch, table)
    result = helper.apply_helper(paths, True)
    assert result['errors'] == [] and result.get('attempts') == 2
    data = state(paths)
    assert data['previous_default_mapping'] == original
    assert data['games']['42']['previous_mapping'] == original
