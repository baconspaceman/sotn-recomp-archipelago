#!/bin/bash
# Regenerates apworld/sotn_recomp/data/castle from the pinned SOTN-Shuffler and BIN-Patcher commits and
# the disc, then checks the port against the JS for a few seeds.
#
# usage: tools/castle/regen.sh DISC [WORKDIR]
#   DISC     the US disc image (SLUS-00067, track 1); only read
#   WORKDIR  where SOTN-Shuffler is (or gets) checked out, as WORKDIR/shuffler (default: a temp folder)
#
# Needs git, node and npm (first run only, to install the shuffler's two node packages), and Python 3.12.
set -euo pipefail

SHUFFLER_COMMIT=efb1db1a964bb2000cde2f668d8d9a099165fb32
BIN_PATCHER_COMMIT=cf4d99116e41e6998a65e3d64c8af69ac754c894

DISC=$(realpath "$1")
WORK=${2:-$(mktemp -d)}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
SHUFFLER="$WORK/shuffler"

if [ ! -d "$SHUFFLER/.git" ]; then
    git clone https://github.com/SestrenExsis/SOTN-Shuffler.git "$SHUFFLER"
fi
git -C "$SHUFFLER" checkout --quiet "$SHUFFLER_COMMIT"
git -C "$SHUFFLER" submodule update --init --quiet
if [ "$(git -C "$SHUFFLER/lib/BIN-Patcher" rev-parse HEAD)" != "$BIN_PATCHER_COMMIT" ]; then
    echo "lib/BIN-Patcher is not at $BIN_PATCHER_COMMIT" >&2
    exit 1
fi
if [ ! -d "$SHUFFLER/node_modules" ]; then
    (cd "$SHUFFLER" && npm ci --ignore-scripts)
fi

node "$HERE/dump_data.mjs" --shuffler "$SHUFFLER" --disc "$DISC"

PYTHON=${PYTHON:-"$ROOT/ref/archipelago/.venv/Scripts/python"}
"$PYTHON" -I "$HERE/check_parity.py" --shuffler "$SHUFFLER" --disc "$DISC" --seeds TestSeedA,TestSeedB,TestSeedC
