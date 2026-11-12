#!/usr/bin/env bash
# Smoke test for a running API (used by CI against `docker compose up`).
#
#   scripts/smoke-test.sh [BASE_URL]            default: http://localhost:8000
#
# With LUMINA_SMOKE_EMAIL and LUMINA_SMOKE_PASSWORD (a platform admin), it also signs in,
# writes (and cleans up), and signs out. Needs only curl and python3. Exits non-zero on the
# first failed check.
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API="$BASE_URL/api/v1"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

pass() { printf '  ok   %s\n' "$1"; }
fail() {
    printf '  FAIL %s\n' "$1" >&2
    printf '       response: %s\n' "$(head -c 500 "$BODY")" >&2
    exit 1
}

# request METHOD URL [curl args...] -> prints the HTTP status, body goes to $BODY
request() {
    local method="$1" url="$2"
    shift 2
    curl -sS -o "$BODY" -w '%{http_code}' -X "$method" "$url" "$@"
}

# json_get EXPRESSION -> evaluates a Python expression against the JSON body (bound to `d`)
json_get() {
    python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' \
        "$BODY" "$1"
}

expect_status() {
    local expected="$1" actual="$2" label="$3"
    [ "$actual" = "$expected" ] || fail "$label: expected HTTP $expected, got $actual"
}

echo "Smoke testing $BASE_URL"

status=$(request GET "$BASE_URL/health/live")
expect_status 200 "$status" "liveness"
pass "GET /health/live"

status=$(request GET "$BASE_URL/health/ready")
expect_status 200 "$status" "readiness"
[ "$(json_get 'd["checks"]["database"]')" = "up" ] || fail "readiness: database is not up"
pass "GET /health/ready reports database: up"

status=$(request GET "$API/courses")
expect_status 200 "$status" "public course list"
pass "GET /courses (published courses: $(json_get 'd["total"]'))"

status=$(request GET "$API/courses?status=draft")
expect_status 403 "$status" "drafts are not public"
[ "$(json_get 'd["error"]["code"]')" = "forbidden" ] || fail "drafts: wrong error code"
pass "GET /courses?status=draft is forbidden for the public"

status=$(request POST "$API/subjects" -H 'Content-Type: application/json' -d '{"name": "Smoke"}')
expect_status 401 "$status" "anonymous write"
pass "anonymous POST /subjects is rejected"

if [ -n "${LUMINA_SMOKE_EMAIL:-}" ] && [ -n "${LUMINA_SMOKE_PASSWORD:-}" ]; then
    credentials=$(python3 -c 'import json, os; print(json.dumps({
        "email": os.environ["LUMINA_SMOKE_EMAIL"], "password": os.environ["LUMINA_SMOKE_PASSWORD"]}))')
    status=$(request POST "$API/auth/login" -H 'Content-Type: application/json' -d "$credentials")
    expect_status 200 "$status" "sign in"
    auth="Authorization: Bearer $(json_get 'd["access_token"]')"
    pass "POST /auth/login"

    status=$(request GET "$API/me" -H "$auth")
    expect_status 200 "$status" "current user"
    [ "$(json_get 'd["role"]')" = "platform_admin" ] || fail "the smoke account is not a platform admin"
    pass "GET /me (platform_admin)"

    name="Smoke Test $(date +%s)-$$"
    status=$(request POST "$API/subjects" -H "$auth" -H 'Content-Type: application/json' \
        -d "{\"name\": \"$name\"}")
    expect_status 201 "$status" "admin write"
    subject_id=$(json_get 'd["id"]')
    pass "POST /subjects created '$name'"

    status=$(request DELETE "$API/subjects/$subject_id" -H "$auth")
    expect_status 204 "$status" "admin delete"
    pass "DELETE /subjects/{id} cleaned up"

    # Upload a small file the way a browser does: signed form straight to the storage.
    upload_file="$(mktemp)"
    printf 'smoke test upload\n' > "$upload_file"
    size=$(wc -c < "$upload_file" | tr -d ' ')
    status=$(request POST "$API/media" -H "$auth" -H 'Content-Type: application/json' \
        -d "{\"kind\": \"document\", \"filename\": \"smoke.txt\", \"content_type\": \"text/plain\", \"size_bytes\": $size}")
    expect_status 201 "$status" "start upload"
    media_id=$(json_get 'd["media"]["id"]')
    upload_url=$(json_get 'd["upload"]["url"]')
    form_args=()
    while IFS= read -r field; do form_args+=(-F "$field"); done < <(
        json_get '"\n".join(f"{k}={v}" for k, v in d["upload"]["fields"].items())'
    )
    stored=$(curl -sS -o /dev/null -w '%{http_code}' "${form_args[@]}" \
        -F "file=@$upload_file;type=text/plain" "$upload_url")
    rm -f "$upload_file"
    [ "$stored" = "204" ] || [ "$stored" = "201" ] || [ "$stored" = "200" ] \
        || fail "storage upload: got HTTP $stored from $upload_url"
    status=$(request POST "$API/media/$media_id/complete" -H "$auth")
    expect_status 200 "$status" "complete upload"
    [ "$(json_get 'd["status"]')" = "ready" ] || fail "upload not ready: $(json_get 'd.get("rejection_reason")')"
    pass "media upload through the storage (signed form) is ready"

    status=$(request POST "$API/auth/logout" -H "$auth")
    expect_status 204 "$status" "sign out"
    status=$(request GET "$API/me" -H "$auth")
    expect_status 401 "$status" "token after sign-out"
    pass "POST /auth/logout ends the session"
else
    echo "  skip signed-in checks (set LUMINA_SMOKE_EMAIL and LUMINA_SMOKE_PASSWORD)"
fi

echo "Smoke test passed."
