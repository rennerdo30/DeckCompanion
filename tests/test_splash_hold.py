"""Owner-requested splash defaults and bounded hold allowance; no real child process."""
import json
from pathlib import Path
import subprocess

import pytest
from deckcompanion import cli, splash
from deckcompanion.common import save_state


def test_four_second_default_and_ten_second_clamp(paths):
    def settings(*args):
        return cli.execute(cli.parser().parse_args(['settings', *args]), paths)
    assert settings()['splash_seconds'] == 4
    assert settings('--set', 'splash_seconds=9')['splash_seconds'] == 9
    assert settings('--splash-seconds', '99')['splash_seconds'] == 10
    assert splash.settings({'settings': {'splash_seconds': 99}})['splash_seconds'] == 10


@pytest.mark.parametrize('seconds,expected', [(1, 123), (4, 126), (10, 130)])
def test_engine_wait_covers_hold_cap_and_kills_only_owned_pid(paths, monkeypatch, seconds, expected):
    save_state(paths, {'games': {}, 'settings': {'splash_seconds': seconds}})
    events = []
    class Process:
        pid = 9321
        def wait(self, timeout):
            events.append(('wait', timeout))
            if timeout != 2: raise subprocess.TimeoutExpired('splash', timeout)
        def kill(self):
            events.append(('kill', self.pid))
    def spawn(args, **kwargs):
        payload = json.loads(Path(args[2]).read_text(encoding='utf-8'))
        assert payload['duration_seconds'] == seconds
        return Process()
    monkeypatch.setattr(splash.subprocess, 'Popen', spawn)
    splash.show(paths, {'appid': '42', 'name': 'Fixture'}, {'DISPLAY': ':mock', 'DECKCOMPANION_UI': 'mock-ui'})
    assert events == [('wait', expected), ('kill', 9321), ('wait', 2)]
    assert expected >= seconds + 120 and expected <= 130
    assert splash.STARTED_PIDS[-1] == 9321
    assert 'continuing launch' in (paths.data / 'logs/42-last.log').read_text()
