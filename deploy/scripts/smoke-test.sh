#!/usr/bin/env bash
# Post-deployment smoke test for a real server.
#
# Usage:
#   DOMAIN=app.example.com METRICS_TOKEN=... bash deploy/scripts/smoke-test.sh
#
# Every check prints ok/FAIL and the script exits non-zero if anything failed,
# so it is safe to gate a deployment on it. It never performs a real employer
# application submission and never prints the metrics token.
#
# Expectations follow deploy/nginx/applypilot.conf and the backend behaviour:
#   * `server_tokens off` still advertises a bare `Server: nginx`; only a
#     version string or an upstream (uvicorn) banner is a failure.
#   * /api/v1/metrics is rejected by Nginx with 403 for non-monitoring client
#     addresses and by FastAPI with 401 when the token is missing, so both are
#     correct "not exposed" answers.
#   * the live-view WebSocket rejects a foreign Origin before the handshake
#     (HTTP 403) and, for an allowed Origin with a bad ticket, accepts the
#     handshake only to close it immediately with a 44xx code.

set -uo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN}"
BASE="https://$DOMAIN"
METRICS_TOKEN="${METRICS_TOKEN:-}"
ACCESS_LOG="${ACCESS_LOG:-/var/log/nginx/applypilot.access.log}"
FAILURES=0
SKIPS=0

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
skip() { printf '  skip %s\n' "$1"; SKIPS=$((SKIPS + 1)); }

if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required." >&2
    exit 2
fi

check_status() {
    local label="$1" url="$2" expected="$3"
    local actual
    actual="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url")"
    if [[ "$actual" == "$expected" ]]; then pass "$label ($actual)"; else fail "$label (got ${actual:-no response}, want $expected)"; fi
}

# Counts how many times a header name appears in a header dump. Duplicate
# security headers (Nginx + FastAPI/Next both setting one) are a defect.
header_count() {
    local name="$1" dump="$2"
    grep -ci "^${name}:" <<<"$dump" || true
}

echo "1. HTTP -> HTTPS redirect"
redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "http://$DOMAIN/api/v1/health")"
if [[ "$redirect" == 301* || "$redirect" == 308* ]]; then pass "http redirects"; else fail "http redirect ($redirect)"; fi

echo "2. Health and readiness"
check_status "health" "$BASE/api/v1/health" 200
health_body="$(curl -s --max-time 15 "$BASE/api/v1/health")"
if [[ "$health_body" == *'"status":"healthy"'* ]]; then pass "health payload"; else fail "health payload: $health_body"; fi

ready_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/api/v1/ready")"
ready="$(curl -s --max-time 20 "$BASE/api/v1/ready")"
if [[ "$ready_code" == "200" ]]; then pass "ready ($ready_code)"; else fail "ready (got $ready_code, want 200): $ready"; fi
if [[ "$ready" == *'"status":"ready"'* ]]; then pass "readiness status"; else fail "readiness status: $ready"; fi
if [[ "$ready" == *'"name":"database","ok":true'* ]]; then pass "readiness: database reachable"; else fail "readiness: database check"; fi
if [[ "$ready" == *'"active_browser_sessions"'* ]]; then pass "readiness reports browser sessions"; else fail "readiness missing browser count"; fi
if [[ "$ready" == *'"browser_streaming_available":true'* ]]; then pass "readiness: browser streaming available"; else fail "readiness: browser streaming unavailable"; fi

echo "3. Documentation is not public"
check_status "/docs" "$BASE/docs" 404
check_status "/redoc" "$BASE/redoc" 404
check_status "/openapi.json" "$BASE/openapi.json" 404

echo "4. Security headers (present exactly once)"
headers="$(curl -sI --max-time 15 "$BASE/api/v1/health")"
for header in strict-transport-security x-content-type-options x-frame-options \
              referrer-policy content-security-policy permissions-policy \
              x-request-id; do
    count="$(header_count "$header" "$headers")"
    if [[ "$count" -eq 1 ]]; then
        pass "$header"
    elif [[ "$count" -eq 0 ]]; then
        fail "$header missing"
    else
        fail "$header sent $count times (duplicate header)"
    fi
done

# `server_tokens off` removes the version, not the header itself. Nginx emits a
# bare `Server: nginx`; that is acceptable. A version number or an upstream
# server banner (uvicorn) is not.
server_line="$(grep -i '^server:' <<<"$headers" | head -1 | tr -d '\r')"
if [[ -z "$server_line" ]]; then
    pass "server header absent"
elif grep -Eqi '^server:[[:space:]]*nginx/[0-9]' <<<"$server_line"; then
    fail "nginx version disclosed: ${server_line#*: }"
elif grep -Eqi 'uvicorn|python|starlette|werkzeug' <<<"$server_line"; then
    fail "upstream server banner disclosed: ${server_line#*: }"
else
    pass "server header has no version (${server_line#*: })"
fi

echo "5. Auth surface"
check_status "unauthenticated /auth/me is 401" "$BASE/api/v1/auth/me" 401
login_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/api/v1/auth/google/login")"
if [[ "$login_code" == 302 || "$login_code" == 307 ]]; then pass "login redirect"; else fail "login redirect (got $login_code)"; fi

echo "6. CORS allow-list"
origin="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X OPTIONS "$BASE/api/v1/health" \
    -H "Origin: https://$DOMAIN" -H "Access-Control-Request-Method: GET")"
if [[ "$origin" == 200 ]]; then pass "same-origin preflight"; else fail "preflight (got $origin)"; fi
foreign="$(curl -s -i --max-time 15 -X OPTIONS "$BASE/api/v1/health" \
    -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET" | tr -d '\r')"
if grep -qi '^access-control-allow-origin: https://evil.example' <<<"$foreign"; then
    fail "foreign origin reflected"
else
    pass "foreign origin not reflected"
fi

echo "7. Metrics protection"
metrics_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/api/v1/metrics")"
case "$metrics_code" in
    403) pass "metrics blocked at the edge by the Nginx allow-list (403)" ;;
    401) pass "metrics requires a bearer token (401)" ;;
    200) fail "metrics is publicly readable without a token" ;;
    *)   fail "metrics returned unexpected status $metrics_code" ;;
esac

if [[ -z "$METRICS_TOKEN" ]]; then
    skip "metrics token not provided (set METRICS_TOKEN to test the payload)"
else
    # The allow-list only admits loopback/monitoring addresses, so the token
    # path is exercised against the backend directly when this runs on the host.
    backend_local="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:8000/api/v1/health")"
    if [[ "$backend_local" == "200" ]]; then
        if curl -s --max-time 10 "http://127.0.0.1:8000/api/v1/metrics" \
                -H "Authorization: Bearer $METRICS_TOKEN" | grep -q applypilot_http_requests_total; then
            pass "metrics payload with token (backend on loopback)"
        else
            fail "metrics payload with token (backend on loopback)"
        fi
        got="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:8000/api/v1/metrics")"
        if [[ "$got" == "401" ]]; then pass "metrics without token on loopback is 401"; else fail "metrics without token on loopback (got $got, want 401)"; fi
    else
        skip "backend not reachable on 127.0.0.1:8000; run this on the host to test the token path"
    fi
fi

echo "8. TLS"
if command -v openssl >/dev/null 2>&1; then
    expiry="$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
    if [[ -n "$expiry" ]]; then
        if end_epoch="$(date -d "$expiry" +%s 2>/dev/null)" && \
           [[ $((end_epoch - $(date +%s))) -gt $((14 * 86400)) ]]; then
            pass "certificate valid > 14 days"
        else
            fail "certificate expires soon: $expiry"
        fi
    else
        fail "could not read certificate"
    fi
else
    skip "openssl not installed"
fi

echo "9. WebSocket endpoint (Origin + ticket enforcement)"
ws_foreign="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    -H "Origin: https://evil.example" \
    "$BASE/api/v1/browser-sessions/view/ws?ticket=invalid")"
if [[ "$ws_foreign" == "403" ]]; then
    pass "foreign Origin is refused before the handshake (403)"
else
    fail "foreign Origin WS upgrade (got $ws_foreign, want 403)"
fi

ws_out="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time 5 \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    -H "Origin: https://$DOMAIN" \
    "$BASE/api/v1/browser-sessions/view/ws?ticket=invalid")"
ws_code="${ws_out%% *}"
ws_time="${ws_out##* }"
# 101 means the handshake completed and the server then closed with a 44xx
# reason code, which is the documented contract for a bad ticket.
if [[ "$ws_code" == "101" || "$ws_code" == "401" || "$ws_code" == "403" ]]; then
    pass "invalid ticket does not stream (HTTP $ws_code)"
else
    fail "invalid ticket WS (got ${ws_code:-no response})"
fi
if [[ -n "$ws_time" ]] && awk "BEGIN{exit !($ws_time < 4)}"; then
    pass "unauthenticated socket is not held open (${ws_time}s)"
else
    fail "unauthenticated socket stayed open until the timeout"
fi

echo "10. Access log excludes query strings"
if [[ -r "$ACCESS_LOG" ]]; then
    leaked="$(grep -c '"GET [^"]*?[^"]* ' "$ACCESS_LOG" || true)"
    if [[ "${leaked:-0}" -eq 0 ]]; then
        pass "no query strings in $ACCESS_LOG"
    else
        fail "$leaked access-log line(s) contain a query string"
    fi
else
    skip "access log not readable ($ACCESS_LOG); run with sudo on the host"
fi

echo
if [[ $FAILURES -eq 0 ]]; then
    printf 'SMOKE TEST PASSED (%d skipped)\n' "$SKIPS"
    exit 0
fi
printf 'SMOKE TEST FAILED: %d check(s) failed (%d skipped)\n' "$FAILURES" "$SKIPS"
exit 1
