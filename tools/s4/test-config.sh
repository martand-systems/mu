#!/usr/bin/env bash
# Tests for relay/gen-credentials.sh and relay/entrypoint.sh that need no Docker and no chisel: what the credential
# tool writes, what the entrypoint turns it into, and that the entrypoint refuses every malformed input instead of
# starting chisel with it. The rules' effect on a real chisel server is tested by run.sh (rows X1-X17).
#
#   tools/s4/test-config.sh        exit 0 only if every line says ok
#
# One check only bites on a Mac: "an upper-case name" is refused everywhere, but it is the Mac's bash 3.2 in a
# UTF-8 locale that would accept it without the scripts' LC_ALL=C. CI runs this file on macOS too for that reason,
# once more with that line taken out (it must then fail). The entrypoint's own LC_ALL line cannot be tested from
# here: E() below starts it with an empty environment.
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
refused_g "a compute port with a leading zero (the same port to chisel)" add-client compute other 07001
refused_g "a new compute port with a leading zero" add-client compute other 07003
refused_g "a workstation port with a leading zero" add-client workstation x 7001+07002
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

echo "-- gen-credentials.sh: the pairing service's admin token and RELAY_REVOKED"
refused_g "login-bundle before there is an admin token" login-bundle avi-mac
G admin-token >/dev/null 2>"$T/admin.err"
tok="$(sed -n 's/^RELAY_ADMIN_TOKEN=//p' "$T/relay.env")"
is "admin-token writes mra_ and 43 characters of base64url" "$(printf '%s' "$tok" | sed -n 's/^mra_[A-Za-z0-9_-]\{43\}$/shape-ok/p')" "shape-ok"
is "admin-token prints nothing secret" "$(grep -c -F -e "${tok:-none}" "$T/admin.err")" "0"
refused_g "a second admin-token" admin-token
G admin-token --rotate >/dev/null 2>&1
tok2="$(sed -n 's/^RELAY_ADMIN_TOKEN=//p' "$T/relay.env")"
if [ -n "$tok2" ] && [ "$tok2" != "$tok" ]; then ok "admin-token --rotate changes it"; else bad "admin-token --rotate changes it"; fi
is "env prints the token's line too" "$(G env | cut -d= -f1 | tr '\n' ' ')" "RELAY_KEY RELAY_CLIENTS RELAY_ADMIN_TOKEN "
G revoke old-laptop >/dev/null 2>&1; G revoke office-pc >/dev/null 2>&1
is "revoke adds RELAY_REVOKED" "$(G env | sed -n 's/^RELAY_REVOKED=//p')" "old-laptop,office-pc"
refused_g "revoking a name twice" revoke office-pc
refused_g "revoking an upper-case name" revoke Mac
G unrevoke old-laptop >/dev/null 2>&1
is "unrevoke removes it" "$(G env | sed -n 's/^RELAY_REVOKED=//p')" "office-pc"
G unrevoke office-pc >/dev/null 2>&1
is "and the line goes with the last name" "$(G env | grep -c '^RELAY_REVOKED')" "0"
G login-bundle avi-mac > "$T/bundle" 2>/dev/null
is "login-bundle to a pipe: the client's lines and the token" "$(cut -d= -f1 "$T/bundle" | tr '\n' ' ')" "RELAY_URL RELAY_FINGERPRINT RELAY_ROLE RELAY_USER RELAY_PASSWORD RELAY_PORTS RELAY_ADMIN_TOKEN "
refused_g "login-bundle of a compute client" login-bundle office-pc
if python3 -c 'import os,pty,sys; st=pty.spawn(sys.argv[1:]); sys.exit(1 if os.waitstatus_to_exitcode(st)==0 else 0)' \
     "$ROOT/relay/gen-credentials.sh" --env "$T/relay.env" login-bundle avi-mac > "$T/tty.out" 2>&1 && ! grep -q -F -e "$tok2" "$T/tty.out"; then
  ok "login-bundle refuses a terminal"
else
  bad "login-bundle refuses a terminal"
fi

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
rc="$(E RELAY_KEY="$RELAY_KEY" RELAY_CLIENTS="$RELAY_CLIENTS" RELAY_ADMIN_TOKEN="")"
is "an empty RELAY_ADMIN_TOKEN is the legacy path, unchanged" "$rc $(cat "$T/args")" "0 server --host 0.0.0.0 --port 8080 --reverse --authfile $T/run/users.json --keepalive 25s"
rc="$(E RELAY_KEY="$RELAY_KEY" RELAY_CLIENTS="$RELAY_CLIENTS" RELAY_REVOKED=avi-mac)"
is "RELAY_REVOKED takes the client out of the legacy auth file" "$rc $(grep -c 'avi-mac' "$T/run/users.json") $(grep -c 'office-pc' "$T/run/users.json")" "0 0 1"

echo "-- entrypoint.sh in pairing mode (RELAY_ADMIN_TOKEN set)"
K="ck-abc"; P="0123456789abcdef01234567"
A() {  # A VAR=VALUE...   the entrypoint in pairing mode, mu-relayd replaced by a stand-in that records its arguments
  rm -f "$T/run/static.tsv" "$T/run/relayd.conf" "$T/run/users.json" "$T/args" "$T/env"
  env -i PATH="$PATH" RELAY_RUN_DIR="$T/run" RELAY_CHISEL="$T/chisel" RELAY_RELAYD="$T/chisel" "$@" sh "$ROOT/relay/entrypoint.sh" > "$T/entry.log" 2>&1
  echo $?
}
rc="$(A RELAY_KEY="$RELAY_KEY" RELAY_CLIENTS="$RELAY_CLIENTS" RELAY_ADMIN_TOKEN="$tok2" RELAY_REVOKED=office-pc)"
is "accepted, and mu-relayd started with its settings file" "$rc $(cat "$T/args")" "0 --config $T/run/relayd.conf"
is "mu-relayd's environment holds no secret and no RELAY_ variable" "$(grep -c -e '^RELAY_' -e 'ck-' -e 'mra_' "$T/env")" "0"
is "static.tsv: the clients not revoked, mode 600" "$(cut -f1,2 "$T/run/static.tsv" | tr '\t\n' ': ') $(ls -l "$T/run/static.tsv" | cut -c1-10)" "workstation:avi-mac  -rw-------"
is "relayd.conf holds the key and the token, mode 600" "$(grep -c -e "^key=$RELAY_KEY\$" -e "^admin_token=$tok2\$" "$T/run/relayd.conf") $(ls -l "$T/run/relayd.conf" | cut -c1-10)" "2 -rw-------"
is "the entrypoint does not write users.json itself" "$([ -e "$T/run/users.json" ] && echo yes || echo no)" "no"
is "nothing secret on the entrypoint's output" "$(grep -c -F -e "$tok2" -e "$RELAY_KEY" "$T/entry.log")" "0"
refused_a() {  # refused_a WHAT VAR=VALUE...   exit 64, mu-relayd not started, nothing written, the token not printed
  local what="$1" rc; shift; rc="$(A "$@")"
  if [ "$rc" = 64 ] && [ ! -e "$T/args" ] && [ ! -e "$T/run/relayd.conf" ] && ! grep -q -F -e '0123456789012345' -e "$tok2" "$T/entry.log"; then ok "entrypoint refuses: $what"
  else bad "entrypoint refuses: $what (exit $rc, started: $([ -e "$T/args" ] && echo yes || echo no))"; fi
}
refused_a "a token without mra_" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="0123456789012345678901234567890123456789012345"
refused_a "a token that is too short" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="mra_0123456789"
refused_a "a token with a quote" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="mra_0123456789012345678901234567890123456789012\"x"
refused_a "a token with a newline" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="mra_0123456789012345678901234567890123456789012
key=ck-evil"
refused_a "RELAY_REVOKED with a space" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="$tok2" RELAY_REVOKED="a, b"
refused_a "a malformed client, as without pairing" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:07001" RELAY_ADMIN_TOKEN="$tok2"
rc="$(A RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_ADMIN_TOKEN="$tok2" RELAY_REVOKED=a)"
is "pairing mode with every static client revoked starts (mu-relayd keeps the sentinel)" "$rc $(wc -c < "$T/run/static.tsv" | tr -d ' ')" "0 0"

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
refused_e "the same compute port twice, once with a leading zero" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001,compute:b:$P:07001"
refused_e "a compute port with a leading zero" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:07001"
refused_e "a workstation port with a leading zero" RELAY_KEY="$K" RELAY_CLIENTS="workstation:a:$P:7001+07002"
refused_e "a port of zeros" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:00000"
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
refused_e "every client revoked (chisel with no users lets anyone in)" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001,workstation:b:$P:7001" RELAY_REVOKED="a,b"
refused_e "RELAY_REVOKED with a character outside the names" RELAY_KEY="$K" RELAY_CLIENTS="compute:a:$P:7001" RELAY_REVOKED='a;b'

echo
if [ "$N_FAIL" -eq 0 ]; then echo "all ok"; else echo "$N_FAIL failed"; exit 1; fi
