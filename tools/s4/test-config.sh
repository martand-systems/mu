#!/usr/bin/env bash
# Tests for relay/gen-credentials.sh and relay/entrypoint.sh that need no Docker and no chisel: what the credential
# tool writes, what the entrypoint turns it into, and that the entrypoint refuses every malformed input instead of
# starting chisel with it. The rules' effect on a real chisel server is tested by run.sh (rows X1-X14).
#
#   tools/s4/test-config.sh        exit 0 only if every line says ok
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/run"
N_FAIL=0
ok()   { printf 'ok    %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; N_FAIL=$((N_FAIL + 1)); }
is()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1: got '$2', expected '$3'"; fi; }
G()    { "$ROOT/relay/gen-credentials.sh" --env "$T/relay.env" "$@"; }
refused_g() { local what="$1"; shift; if G "$@" >/dev/null 2>&1; then bad "gen-credentials accepted: $what"; else ok "gen-credentials refuses: $what"; fi; }

# A stand-in for chisel: records its arguments and environment and exits. The entrypoint clears the environment,
# so the place to record to is written into the stand-in itself.
printf '#!/bin/sh\nprintf "%%s\\n" "$*" > "%s/args"\nenv > "%s/env"\n' "$T" "$T" > "$T/chisel"
chmod +x "$T/chisel"
E() {  # E VAR=VALUE...   runs the entrypoint with exactly these variables; prints its exit code
  rm -f "$T/run/users.json" "$T/args" "$T/env"
  env -i PATH="$PATH" RELAY_RUN_DIR="$T/run" RELAY_CHISEL="$T/chisel" "$@" sh "$ROOT/relay/entrypoint.sh" > "$T/entry.log" 2>&1
  echo $?
}
refused_e() {  # refused_e WHAT VAR=VALUE...   exit 64, chisel not started, no auth file left
  local what="$1" rc; shift; rc="$(E "$@")"
  if [ "$rc" = 64 ] && [ ! -e "$T/args" ] && [ ! -e "$T/run/users.json" ]; then ok "entrypoint refuses: $what"
  else bad "entrypoint refuses: $what (exit $rc, chisel started: $([ -e "$T/args" ] && echo yes || echo no))"; fi
}

echo "-- gen-credentials.sh"
fp="$(G init 2>/dev/null | sed -n 's/^RELAY_FINGERPRINT=//p')"
is "init prints a 44-character fingerprint ending in =" "${#fp} ${fp: -1}" "44 ="
is "the state file is mode 600" "$(ls -l "$T/relay.env" | cut -c1-10)" "-rw-------"
refused_g "a second init" init
G add-client compute office-pc 7001 > "$T/c.env" 2>/dev/null; G add-client workstation avi-mac 7001+7002 > "$T/w.env" 2>/dev/null
is "add-client prints the client's settings" "$(sed -n 's/^RELAY_USER=//p; s/^RELAY_PORTS=//p; s/^RELAY_ROLE=//p' "$T/c.env" | tr '\n' ' ')" "compute office-pc 7001 "
pw="$(sed -n 's/^RELAY_PASSWORD=//p' "$T/w.env")"
is "passwords are 48 hex characters" "$(printf '%s' "$pw" | tr -d '0-9a-f' | wc -c | tr -d ' ') ${#pw}" "0 48"
is "fingerprint is stable" "$(G fingerprint)" "$fp"
refused_g "a name used twice" add-client workstation office-pc 7001
refused_g "a compute port used twice" add-client compute other 7001
refused_g "a port outside 7001-7999" add-client workstation x 8080
refused_g "a port that is not a number" add-client compute x 70a1
refused_g "an unknown role" add-client admin x 7001
refused_g "two ports for a compute node" add-client compute x 7003+7004
refused_g "a trailing +" add-client workstation x 7001+
refused_g "an upper-case name" add-client workstation Mac 7001
refused_g "removing a client that does not exist" remove-client nobody
G rotate-client avi-mac >/dev/null 2>&1
pw2="$(G client avi-mac 2>/dev/null | sed -n 's/^RELAY_PASSWORD=//p')"
if [ -n "$pw2" ] && [ "$pw2" != "$pw" ]; then ok "rotate-client changes the password"; else bad "rotate-client changes the password"; fi
is "rotate-client keeps role and ports" "$(G show | sed -n 's/^workstation *avi-mac *ports //p')" "7001+7002"
G add-client compute tmp 7005 >/dev/null 2>&1; G remove-client tmp >/dev/null 2>&1
is "remove-client removes it" "$(G show | grep -c tmp)" "0"
is "show prints no password" "$(G show | grep -c -F -e "$pw2")" "0"
is "env prints exactly the two variables" "$(G env | cut -d= -f1 | tr '\n' ' ')" "RELAY_KEY RELAY_CLIENTS "
G rotate-key >/dev/null 2>&1
if [ "$(G fingerprint)" != "$fp" ]; then ok "rotate-key changes the fingerprint"; else bad "rotate-key changes the fingerprint"; fi

echo "-- entrypoint.sh with what gen-credentials.sh wrote"
set -a; . "$T/relay.env"; set +a
rc="$(E RELAY_KEY="$RELAY_KEY" RELAY_CLIENTS="$RELAY_CLIENTS")"
is "accepted" "$rc" "0"
is "chisel's arguments" "$(cat "$T/args")" "server --host 0.0.0.0 --port 8080 --reverse --authfile $T/run/users.json --keepalive 25s"
is "chisel gets the key and no RELAY_ variable" "$(grep -c '^CHISEL_KEY_FILE=ck-' "$T/env") $(grep -c '^RELAY_' "$T/env")" "1 0"
is "nothing secret on the entrypoint's output" "$(grep -c -F -e "$pw2" -e "$RELAY_KEY" "$T/entry.log")" "0"
is "the auth file is mode 600" "$(ls -l "$T/run/users.json" | cut -c1-10)" "-rw-------"
python3 - "$T/run/users.json" "$pw2" <<'PY' && ok "the rules allow exactly the client's own tunnel" || bad "the rules allow exactly the client's own tunnel"
import json, re, sys
users = json.load(open(sys.argv[1]))
rules = {k.split(":")[0]: v for k, v in users.items()}
assert set(rules) == {"office-pc", "avi-mac"}, rules
assert "avi-mac:" + sys.argv[2] in users
def allowed(user, addr):                      # chisel: any rule that matches the address
    return any(re.search(r, addr) for r in rules[user])
yes = [("office-pc", "R:127.0.0.1:7001"), ("avi-mac", "127.0.0.1:7001"), ("avi-mac", "127.0.0.1:7002")]
no = [("office-pc", "R:127.0.0.1:7002"), ("office-pc", "R:0.0.0.0:7001"), ("office-pc", "R:127.0.0.1:70010"),
      ("office-pc", "127.0.0.1:7001"), ("office-pc", "xR:127.0.0.1:7001"), ("office-pc", "R:127x0.0.1:7001"),
      ("office-pc", "socks"), ("avi-mac", "R:127.0.0.1:7001"), ("avi-mac", "127.0.0.1:7003"),
      ("avi-mac", "127.0.0.1:70011"), ("avi-mac", "127.0.0.1:8080"), ("avi-mac", "dokploy-traefik:8080"),
      ("avi-mac", "10.0.0.1:7001"), ("avi-mac", "1127.0.0.1:7001"), ("avi-mac", "127a0b0c1:7001"),
      ("avi-mac", "socks"), ("avi-mac", "127.0.0.1:7001\nx"), ("avi-mac", "evil.example:443")]
for u, a in yes: assert allowed(u, a), (u, a)
for u, a in no: assert not allowed(u, a), (u, a)
PY
rc="$(E RELAY_KEY="$RELAY_KEY" RELAY_CLIENTS="$RELAY_CLIENTS" RELAY_KEEPALIVE=0s)"
is "RELAY_KEEPALIVE reaches chisel" "$rc $(sed -n 's/.*--keepalive //p' "$T/args")" "0 0s"

echo "-- entrypoint.sh refuses"
K="ck-abc"; P="0123456789abcdef01234567"
refused_e "nothing set"
refused_e "no clients" RELAY_KEY="$K"
refused_e "no key" RELAY_CLIENTS="compute:a:$P:7001"
refused_e "a key that is not ck-..." RELAY_KEY="abc" RELAY_CLIENTS="compute:a:$P:7001"
refused_e "a key with a character outside base64" RELAY_KEY='ck-ab"c' RELAY_CLIENTS="compute:a:$P:7001"
refused_e "a client without ports" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P"
refused_e "a client with too many fields" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001:9"
refused_e "a short password" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:short:7001"
refused_e "an empty password" RELAY_KEY="$K" RELAY_CLIENTS="compute:a::7001"
refused_e "the same compute port twice" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001,compute:b:$P:7001"
refused_e "the same name twice" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001,workstation:a:$P:7001"
refused_e "the relay's own port 8080" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:8080"
refused_e "a workstation port outside the range" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001+22"
refused_e "an unknown role" RELAY_KEY="$K" RELAY_CLIENTS="admin:a:$P:7001"
refused_e "two ports for a compute node" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001+7002"
refused_e "a port that is not a number" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001+x"
refused_e "a regular expression as a port" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:.*"
refused_e "a name with a quote and a regular expression" RELAY_KEY="$K" RELAY_CLIENTS='workstation:a".*:'"$P:7001"
refused_e "an upper-case name" RELAY_KEY="$K" RELAY_CLIENTS="workstation:A:$P:7001"
refused_e "a name starting with -" RELAY_KEY="$K" RELAY_CLIENTS="workstation:-a:$P:7001"
refused_e "an empty name" RELAY_KEY="$K" RELAY_CLIENTS="workstation::$P:7001"
refused_e "a space in the list" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001, compute:b:$P:7002"
refused_e "only commas" RELAY_KEY="$K" RELAY_CLIENTS=",,"
refused_e "a keepalive that is not a duration" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001" RELAY_KEEPALIVE="abc"
refused_e "one bad client among good ones" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001,workstation:b:$P:7001,workstation:c:$P:99999"

echo
if [ "$N_FAIL" -eq 0 ]; then echo "all ok"; else echo "$N_FAIL failed"; exit 1; fi
