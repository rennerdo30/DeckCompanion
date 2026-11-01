#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$root/src/deckcompanion/__main__.py" ]; then
  # A standalone downloaded installer bootstraps only a checksum-verified release.
  timeout 30s python3 - "$@" <<'PY'
import hashlib, json, os, pathlib, subprocess, sys, tarfile, tempfile, urllib.request

def get(url):
    if not url.startswith('https://'):
        raise ValueError('HTTPS required')
    request = urllib.request.Request(url, headers={'User-Agent': 'DeckCompanion-Installer'})
    with urllib.request.urlopen(request, timeout=2) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Insecure redirect')
        data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError('Installer payload too large')
        return data

with tempfile.TemporaryDirectory(prefix='deckcompanion-install-') as temp:
    work = pathlib.Path(temp)
    api = os.environ.get('DECKCOMPANION_RELEASE_API', 'https://api.github.com/repos/rennerdo30/DeckCompanion/releases/latest')
    release = json.loads(get(api))
    index = json.loads(get(next(a['browser_download_url'] for a in release['assets'] if a['name'] == 'index.json')))
    engine = index['engine']
    payload = get(engine['url'])
    if hashlib.sha256(payload).hexdigest() != engine['sha256']:
        raise ValueError('Engine SHA256 mismatch')
    archive = work / 'engine.tar.gz'
    archive.write_bytes(payload)
    root = work / 'engine'
    root.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 128 * 1024 * 1024:
            raise ValueError('Engine expansion too large')
        for member in members:
            path = (root / member.name).resolve()
            if not path.is_relative_to(root) or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe engine archive')
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, path.open('xb') as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
                path.chmod(member.mode & 0o777)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / 'src'), str(root)]))
    proc = subprocess.Popen([sys.executable, '-m', 'deckcompanion', 'install', '--source', str(root), *sys.argv[1:]], env=env)
    owned_pid = proc.pid
    try:
        result = proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        raise
    raise SystemExit(result)
PY
else
  PYTHONPATH="$root/src:$root" timeout 30s python3 -m deckcompanion install --source "$root" "$@"
fi
printf '%s\n' 'Install complete. Apply pending Steam changes now (Steam will restart), or later using:'
printf '%s\n' '  ~/.local/share/deckcompanion/bin/deckcompanion apply --restart-steam'
if [ -t 0 ]; then
  printf 'Apply now and restart Steam? [y/N; N = later] '
  read -r answer
  case "$answer" in
    y|Y) timeout 30s "${DECKCOMPANION_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/deckcompanion}/bin/deckcompanion" apply --restart-steam ;;
  esac
fi
