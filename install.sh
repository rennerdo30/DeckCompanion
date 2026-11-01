#!/bin/sh
set -eu
# A pipe has $0=sh: never mistake its working directory for an extracted release.
root=''
case "$0" in
  */install.sh|install.sh) root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) ;;
esac
if [ -z "$root" ] || [ ! -f "$root/src/deckcompanion/__main__.py" ]; then
  printf '%s\n' 'DeckCompanion: downloading the latest release (no root required).'
  timeout 300s python3 - "$@" <<'PY'
import hashlib, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile, urllib.request

def get(url, limit):
    if not url.startswith('https://'):
        raise ValueError('HTTPS required')
    request = urllib.request.Request(url, headers={'User-Agent': 'DeckCompanion-Installer'})
    with urllib.request.urlopen(request, timeout=15) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Insecure redirect')
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Installer payload too large')
        return data

def run(command, env):
    proc = subprocess.Popen(command, env=env)
    owned_pid = proc.pid
    try:
        result = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()  # Only our recorded subprocess.
        proc.wait(timeout=2)
        raise
    if result:
        raise SystemExit(result)

with tempfile.TemporaryDirectory(prefix='deckcompanion-install-') as temp:
    work = pathlib.Path(temp)
    base = 'https://github.com/rennerdo30/DeckCompanion/releases/latest/download/'
    print('DeckCompanion: downloading release index.', flush=True)
    index = json.loads(get(os.environ.get('DECKCOMPANION_INDEX_URL', base + 'index.json'), 8 * 1024 * 1024))
    engine = index['engine']
    print('DeckCompanion: downloading engine and checking SHA256.', flush=True)
    payload = get(engine['url'], 512 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != engine['sha256']:
        raise ValueError('Engine SHA256 mismatch')
    archive = work / 'engine.tar.gz'
    archive.write_bytes(payload)
    root = work / 'engine'
    root.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 1024 * 1024 * 1024:
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
                    shutil.copyfileobj(src, dst)
                path.chmod(member.mode & 0o777)
    (root / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / 'src'), str(root)]))
    print('DeckCompanion: installing engine; Steam may stay running.', flush=True)
    run([sys.executable, '-m', 'deckcompanion', 'install', '--source', str(root), *sys.argv[1:]], env)
    # Pin apply to the installed payload, never the temporary directory being removed.
    data = pathlib.Path(os.environ.get('DECKCOMPANION_HOME', str(pathlib.Path(os.environ.get('XDG_DATA_HOME', str(pathlib.Path.home() / '.local/share'))) / 'deckcompanion')))
    print('DeckCompanion: applying Steam changes; Steam will restart once if running.', flush=True)
    run([str(data / 'bin/deckcompanion'), 'apply', '--restart-steam', '--json'], os.environ.copy())
    print('DeckCompanion: install complete. A scheduled apply continues independently; check status --json for last_apply.', flush=True)
    raise SystemExit(0)
PY
else
  printf '%s\n' 'DeckCompanion: installing engine; Steam may stay running.'
  PYTHONPATH="$root/src:$root" timeout 60s python3 -m deckcompanion install --source "$root" "$@"
  cli="${DECKCOMPANION_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/deckcompanion}/bin/deckcompanion"
  printf '%s\n' 'DeckCompanion: applying Steam changes; Steam will restart once if running.'
  timeout 30s "$cli" apply --restart-steam --json
  printf '%s\n' 'DeckCompanion: install complete. Check status --json for the detached apply result.'
fi
