#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,11))' 2>/dev/null; then
        exec "$candidate" scripts/build.py "$@"
    fi
done
echo 'Python 3.11+ is required to build. On macOS with Homebrew: brew install python rust' >&2
exit 1
