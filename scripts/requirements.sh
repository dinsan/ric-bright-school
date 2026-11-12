#!/usr/bin/env bash
# Regenerate the pinned requirements files from pyproject.toml (run by `make requirements`).
# "Universal" resolution keeps platform markers, so the same files work on Linux, macOS and
# Windows and on every supported Python version. Needs `uv` (installed by `make install`).
set -euo pipefail
cd "$(dirname "$0")/.."

UV=".venv/bin/uv"
[ -x "$UV" ] || UV="uv"
command -v "$UV" >/dev/null 2>&1 || { echo "uv not found: run make install first" >&2; exit 1; }

compile() {
    local output="$1" header="$2"
    shift 2
    local tmp
    tmp="$(mktemp)"
    "$UV" pip compile pyproject.toml --universal --python-version 3.11 --no-header --quiet \
        --output-file "$tmp" "$@"
    { printf '%s\n' "$header"; cat "$tmp"; } > "$output"
    rm -f "$tmp"
    echo "wrote $output"
}

compile requirements.txt "# Pinned runtime dependencies (what the Docker image installs).
# Generated from pyproject.toml by \`make requirements\` — do not edit by hand.
# Works on Linux, macOS and Windows, Python 3.11+.  Install: pip install -r requirements.txt"

compile requirements-dev.txt "# Pinned runtime + development dependencies (tests, lint, type-check).
# Generated from pyproject.toml by \`make requirements\` — do not edit by hand.
# Install: pip install -r requirements-dev.txt && pip install --no-deps -e .   (or: make install)" \
    --extra dev
