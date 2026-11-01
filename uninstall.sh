#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONPATH="$root/src:$root" exec timeout 30s python3 -m deckcompanion uninstall "$@"
