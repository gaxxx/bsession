#!/usr/bin/env bash
# Tests the bsession shim's path derivation and JSON transport against a mock.
set -uo pipefail
SHIM="$(cd "$(dirname "$0")/.." && pwd)/plugin/bin/bsession"
fail=0

# Wait until a mock at the given port answers POST /cli (retry ~20×0.1s).
wait_ready() {
  local port=$1 i
  for i in $(seq 1 20); do
    curl -s -o /dev/null -X POST "http://127.0.0.1:$port/cli" -d '{}' && return 0
    sleep 0.1
  done
  return 1
}

PORT=18099
REQ=/tmp/bs-shim-req.json
python3 - "$PORT" "$REQ" <<'PY' &
import sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
port, reqfile = int(sys.argv[1]), sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        open(reqfile, "wb").write(self.rfile.read(n))
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(b'{"stdout":"OK","stderr":"","code":0}')
    def log_message(self,*a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
MOCK_PID=$!

# Second mock: always errors (stderr "boom", exit code 2).
ERRPORT=18098
python3 - "$ERRPORT" <<'PY' &
import sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
port = int(sys.argv[1])
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(b'{"stdout":"","stderr":"boom","code":2}')
    def log_message(self,*a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
ERR_PID=$!

wait_ready "$PORT"    || { echo "FAIL: mock $PORT never came up"; fail=1; }
wait_ready "$ERRPORT" || { echo "FAIL: mock $ERRPORT never came up"; fail=1; }
export BSESSION_API_URL="http://127.0.0.1:$PORT"

# Case A: plain command, no form
out=$("$SHIM" --profile demo session list --json)
[ "$out" = "OK" ] || { echo "FAIL: stdout not forwarded (got '$out')"; fail=1; }
grep -q '"argv"' "$REQ" && grep -q '"session"' "$REQ" || { echo "FAIL: argv missing"; fail=1; }
grep -q '"profile":"demo"' "$REQ" || { echo "FAIL: profile override missing"; fail=1; }

# Case B: form path -> skill_id + rel derived
FORMDIR=/tmp/bs-shim/uscis-check/forms
mkdir -p "$FORMDIR"; printf 'person = "x"\n' > "$FORMDIR/wang.toml"
BSESSION_FORM="$FORMDIR/wang.toml" "$SHIM" nav https://x >/dev/null
grep -q '"skill_id":"uscis-check"' "$REQ" || { echo "FAIL: skill_id derivation"; fail=1; }
grep -q '"rel":"forms/wang.toml"' "$REQ" || { echo "FAIL: rel derivation"; fail=1; }

# Case C: exit-code + stderr propagation (error mock)
err=$(BSESSION_API_URL="http://127.0.0.1:$ERRPORT" "$SHIM" session list 2>&1 >/dev/null); rc=$?
[ "$rc" = 2 ] || { echo "FAIL: exit code not propagated (got $rc)"; fail=1; }
echo "$err" | grep -q boom || { echo "FAIL: stderr not forwarded"; fail=1; }

# Case D: unreachable API -> exit 3
err=$(BSESSION_API_URL="http://127.0.0.1:19999" "$SHIM" session list 2>&1 >/dev/null); rc=$?
[ "$rc" = 3 ] || { echo "FAIL: unreachable exit code (got $rc)"; fail=1; }
echo "$err" | grep -qi unreachable || { echo "FAIL: no unreachable message"; fail=1; }

kill "$MOCK_PID" 2>/dev/null
kill "$ERR_PID" 2>/dev/null
rm -rf /tmp/bs-shim
[ "$fail" = 0 ] && echo "PASS shim tests" || echo "shim tests FAILED"
exit $fail
