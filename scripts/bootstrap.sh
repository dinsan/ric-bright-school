#!/usr/bin/env bash
# One-command local setup, run by `make bootstrap`. Safe to run again at any time:
# an existing .env is never overwritten, and every other step is idempotent.
#
#   1. check prerequisites (Python 3.11+ with venv, Docker with Compose v2, running)
#   2. create .env from .env.example, with generated secret and encryption keys
#   3. create .venv and install the pinned dependencies      (make install)
#   4. start PostgreSQL, Mailpit and MinIO in Docker         (make db mail storage)
#   5. apply migrations and load demo data                   (make migrate seed)
#   6. create a local platform admin, if there is none yet   (admin@example.com)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
MAKE="${MAKE:-make}"

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
fail() {
    printf '\nbootstrap failed: %s\n' "$1" >&2
    shift
    for hint in "$@"; do printf '  %s\n' "$hint" >&2; done
    exit 1
}

step "Checking prerequisites"
command -v "$PYTHON" >/dev/null 2>&1 \
    || fail "'$PYTHON' was not found." "Install Python 3.11+, or point to it: make bootstrap PYTHON=python3.12"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || fail "Python 3.11+ is required, but '$PYTHON' is $("$PYTHON" -V 2>&1)." \
        "Point to a newer one: make bootstrap PYTHON=python3.12"
"$PYTHON" -c 'import ensurepip, venv' >/dev/null 2>&1 \
    || fail "The Python 'venv' module is missing." "On Debian/Ubuntu: sudo apt install python3-venv"
command -v docker >/dev/null 2>&1 \
    || fail "Docker is not installed." "Install Docker Desktop or Docker Engine: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 \
    || fail "Docker Compose v2 is not available ('docker compose version' failed)." \
        "Install the Compose plugin: https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 \
    || fail "Docker is installed but not running (or you lack permission to use it)." \
        "Start Docker Desktop or the docker service, then run make bootstrap again."
info "$("$PYTHON" -V 2>&1), Docker Compose $(docker compose version --short 2>/dev/null || echo v2)"

step "Creating .env"
if [ -f .env ]; then
    info ".env already exists, leaving it unchanged."
else
    # Copy the example and fill in a random secret key for signing access tokens.
    "$PYTHON" - <<'PY'
import re
import secrets
from pathlib import Path

text = Path(".env.example").read_text()
for name in ("LUMINA_SECRET_KEY", "LUMINA_ENCRYPTION_KEY"):
    key = secrets.token_urlsafe(48)
    text, count = re.subn(rf"(?m)^{name}=$", f"{name}={key}", text)
    if count != 1:
        raise SystemExit(f"Could not find an empty {name}= line in .env.example")
Path(".env").write_text(text)
PY
    info "Created .env with a generated LUMINA_SECRET_KEY and LUMINA_ENCRYPTION_KEY."
fi

step "Installing the pinned dependencies into .venv"
"$MAKE" --no-print-directory install PYTHON="$PYTHON"

step "Starting PostgreSQL in Docker (port 5432)"
"$MAKE" --no-print-directory db \
    || fail "PostgreSQL did not start." \
        "If port 5432 is already in use by another PostgreSQL, stop it (see README, Troubleshooting)." \
        "Logs: docker compose logs db"

step "Applying database migrations"
"$MAKE" --no-print-directory migrate

step "Starting Mailpit in Docker (catches the API's emails)"
"$MAKE" --no-print-directory mail \
    || fail "Mailpit did not start." \
        "If port 1025 or 8025 is already in use, stop that program and run make bootstrap again." \
        "Logs: docker compose logs mailpit"

step "Starting MinIO in Docker (storage for uploads)"
"$MAKE" --no-print-directory storage \
    || fail "MinIO did not start." \
        "If port 9000 or 9001 is already in use, stop that program and run make bootstrap again." \
        "Logs: docker compose logs minio"

step "Loading demo data"
"$MAKE" --no-print-directory seed

step "Creating a local platform admin"
# The password is generated and printed once, here only. Skipped if the account exists.
.venv/bin/python -m app.scripts.create_admin \
    --email admin@example.com --name "Local Admin" --generate-password --if-missing

cat <<'DONE'

Setup complete. Next steps:
  make api       start the API with auto-reload, then open http://localhost:8000/docs
  make test      run the tests (starts a separate test database on port 5433)
  make lint      lint, format check and type-check

Sign in as admin@example.com with the password shown above (POST /api/v1/auth/login,
or Authorize in /docs). Lost it? Create another admin: make admin EMAIL=you@example.com
Emails the API sends (verification, password reset) arrive at http://localhost:8025
DONE
