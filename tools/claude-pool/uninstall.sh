#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required to run the claude-pool uninstaller." >&2
    exit 1
fi

exec python3 "$script_dir/scripts/uninstall.py" "$@"
