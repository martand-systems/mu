#!/bin/bash
# Fast tests of the job layer (docs/design/job-layer.md §16, "unit level"): no root, no systemd, no network, no ssh
# server. macOS (/bin/bash 3.2, the system python3 and the toolchain's Python) and Linux. A throwaway HOME and MU_HOME
# in a temp folder with a space in its path, stub toolchain versions like tools/test-autoupdate. Each check that
# guards against a fault is run a second time with the fault put in, and must then fail ("must-fail" lines).
#
#   /bin/bash tools/test-jobs/run.sh              (KEEP=1 keeps the temp folder; MU_TEST_PYTHONS="py1 py2" chooses
#                                                  the Pythons; QUICK=1 runs 30 instead of 120 kills)
# The end-to-end tests (systemd, sshd, the relay) are tools/test-jobs/e2e.sh, run by .github/workflows/job-layer.yml.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
REAL_HOME="$HOME"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mu jobs test.XXXXXX")"
cleanup() { if [ "${KEEP:-0}" = 1 ]; then echo "kept: $ROOT"; else chmod -R u+w "${ROOT:?}" 2>/dev/null; rm -rf "${ROOT:?}"; fi; }
trap cleanup EXIT
PASS=0; FAILN=0; FAILED=""
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -30; }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected: [$3]
got:      [$2]"; fi; }
has()  { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "expected to contain: [$3]
in: $2" ;; esac; }
hasnt() { case "$2" in *"$3"*) bad "$1" "must not contain: [$3]
in: $2" ;; *) ok "$1" ;; esac; }
section() { printf '\n%s\n' "$1"; }
# py_section PY NAME [ARGS]: run a section of unit.py; its ok/FAIL lines count here
py_section() {
  local py="$1" out rc; shift
  out="$("$py" -I -S "$HERE/unit.py" "$@" 2>&1)"; rc=$?
  printf '%s\n' "$out" | while IFS= read -r l; do printf '  %s\n' "$l"; done
  PASS=$((PASS + $(printf '%s\n' "$out" | grep -c '^ok ')))
  if [ "$rc" != 0 ] || printf '%s\n' "$out" | grep -q '^Traceback'; then
    FAILN=$((FAILN + ${rc:-1})); [ "$rc" != 0 ] || FAILN=$((FAILN + 1)); FAILED="$FAILED
  - unit.py $* ($(basename "$py"))"
  fi
}
# must_fail NAME FAULT PY SECTION...: the section, with MU_JOBS_FAULT=FAULT, must report at least one FAIL
must_fail() {
  local name="$1" f="$2" py="$3" out n; shift 3
  out="$(MU_JOBS_FAULT="$f" "$py" -I -S "$HERE/unit.py" "$@" 2>&1)"
  n="$(printf '%s\n' "$out" | grep -c '^FAIL ')"
  if [ "$n" -gt 0 ]; then ok "must-fail: $name, with fault $f ($n check(s) failed, as they must)"
  else bad "must-fail: $name, with fault $f: nothing failed" "$out"; fi
}

PYS="${MU_TEST_PYTHONS:-}"
if [ -z "$PYS" ]; then
  PYS="$(command -v python3)"
  case "$(uname -s)" in Darwin) d="$(cat "$REAL_HOME/Library/Mu/default" 2>/dev/null)"; t="$REAL_HOME/Library/Mu/toolchains/$d/env/bin/python" ;;
    *) d="$(cat "$REAL_HOME/.local/share/mu/default" 2>/dev/null)"; t="$REAL_HOME/.local/share/mu/toolchains/$d/env/bin/python" ;; esac
  [ -z "$d" ] || [ ! -x "$t" ] || PYS="$PYS $t"
fi
PY1="${PYS%% *}"

# ---------------------------------------------------------------- isolation
export HOME="$ROOT/home"; mkdir -p "$HOME"
unset MU_VERSION MU_PROJECT XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME MU_JOBS_FAULT MU_NODE_FAULT MU_DISPATCH_FAULT 2>/dev/null || true
export NO_COLOR=1 GIT_CONFIG_NOSYSTEM=1
export MU_HOME="$ROOT/mu home"
STUBS="$ROOT/stubs"; mkdir -p "$STUBS" "$ROOT/cwd"
printf 'μ job layer fast tests · bash %s · %s\n' "$BASH_VERSION" "$ROOT"
for p in $PYS; do printf '  python: %s (%s)\n' "$p" "$("$p" -c 'import sys; print(sys.version.split()[0])')"; done

section "syntax"
if /bin/bash -n "$REPO/bin/mu"; then ok "bin/mu passes /bin/bash -n ($(/bin/bash -c 'echo $BASH_VERSION'))"; else bad "bin/mu: bash -n"; fi
for sh in sh dash bash; do
  command -v "$sh" >/dev/null 2>&1 || { echo "  skip  $sh -n mu-node-gate ($sh not installed)"; continue; }
  if "$sh" -n "$REPO/install/hosts/wsl2/mu-node-gate"; then ok "mu-node-gate passes $sh -n"; else bad "mu-node-gate: $sh -n"; fi
done
for p in $PYS; do
  out="$("$p" -I -S -c 'import ast, sys
for f in sys.argv[1:]:
    ast.parse(open(f, encoding="utf-8").read(), f)' "$REPO"/src/mu/{cli,jobs,node,durable,proto}.py "$HERE/unit.py" 2>&1)"
  eq "src/mu/{cli,jobs,node,durable,proto}.py parse ($("$p" -c 'import sys; print(sys.version.split()[0])'))" "$out" ""
done
eq "dispatcher revs match (bin/mu, install/install.sh)" "$(sed -n 's/^DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/install/install.sh")" \
  "$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/bin/mu")"

# ---------------------------------------------------------------- the gate
section "the gate's grammar (§7.1): 28 requests, each shell gives the same answers"
cat > "$STUBS/fake-mu" <<'S'
#!/bin/sh
# stands in for `mu node serve`: shows what the gate handed over
printf '{"ok":true,"args":"%s"}\n' "$*"
S
chmod +x "$STUBS/fake-mu"
mkgate() {   # mkgate OUT [SED]: the gate with the two machine values written in, as the host script does
  sed -e "s|@MU_BIN@|$STUBS/fake-mu|" -e "s|@MU_KEYS@|$ROOT/keys|" ${2:+-e "$2"} "$REPO/install/hosts/wsl2/mu-node-gate" > "$1"
  chmod +x "$1"
}
mkgate "$ROOT/gate"
printf 'restrict ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEY avi-mac\nrestrict ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOTHEROTHEROTHEROTHEROTHEROTHEROTHEROTHER Bad_Name\n' > "$ROOT/keys"
LONG="hello a=$(printf '%0600d' 0 | tr 0 x)"
# request|expected answer (the start of the JSON line, or the args handed to mu node serve)
CASES="hello|{\"ok\":true,\"args\":\"node serve --key=unknown hello\"}
hello a=1  b=x.y:z,+-_|{\"ok\":true,\"args\":\"node serve --key=unknown hello a=1 b=x.y:z,+-_\"}
status id=20261114-101500-k3f9qa|{\"ok\":true,\"args\":\"node serve --key=unknown status id=20261114-101500-k3f9qa\"}
id|{\"ok\":true,\"args\":\"node serve --key=unknown id\"}
|no-command
hello; id|bad-character
hello \$(id)|bad-character
hello \`id\`|bad-character
hello a=*|bad-character
hello -x|bad-argument
--help|bad-verb
hello a=1 b|bad-argument
hello a=1|id|bad-character
hello a='x'|bad-character
hello a=../../etc|bad-character
HELLO|bad-verb
git-receive-pack 'jatayu-0123456789ab.git'|{\"ok\":true,\"args\":\"node serve --key=unknown receive-pack repo=jatayu-0123456789ab\"}
git-receive-pack '../x.git'|bad-repository
git-receive-pack '/abs/x.git'|bad-repository
git-receive-pack 'a.git' x|bad-character
git-receive-pack 'a b.git'|bad-repository
git-upload-pack 'a.git'|bad-character
git-receive-pack '-x.git'|bad-repository
git-receive-pack 'a'|bad-repository
hello	a=1|bad-character
$LONG|too-long
hello a=1 b=2 c=3 d=4 e=5 f=6 g=7 h=8 i=9 j=10 k=11 l=12 m=13 n=14 o=15 p=16|bad-request"
NL="hello a=1
id"
gate_run() {   # gate_run SHELL GATE REQUEST -> "rc answer"
  local o rc; o="$(SSH_ORIGINAL_COMMAND="$3" "$1" "$2" 2>&1)"; rc=$?
  printf '%s %s' "$rc" "$o"
}
gate_suite() {   # gate_suite SHELL GATE -> prints one line per mismatch; returns the number of them
  local sh="$1" g="$2" n=0 req want got
  while IFS= read -r line; do
    req="${line%|*}"; want="${line##*|}"
    case "$line" in "hello a=1|id|bad-character") req="hello a=1|id" ;; esac
    got="$(gate_run "$sh" "$g" "$req")"
    case "$want" in
      "{"*) [ "$got" = "0 $want" ] || { echo "[$req] expected 0 $want, got $got"; n=$((n + 1)); } ;;
      *) case "$got" in "64 {\"ok\":false,\"error\":\"$want\""*) ;; *) echo "[$req] expected 64 $want, got $got"; n=$((n + 1)) ;; esac ;;
    esac
  done <<EOF
$CASES
EOF
  got="$(gate_run "$sh" "$g" "$NL")"
  case "$got" in "64 {\"ok\":false,\"error\":\"bad-character\""*) ;; *) echo "[newline] expected bad-character, got $got"; n=$((n + 1)) ;; esac
  return "$n"
}
for sh in sh dash bash; do
  command -v "$sh" >/dev/null 2>&1 || continue
  out="$(gate_suite "$sh" "$ROOT/gate")"; n=$?
  if [ "$n" = 0 ]; then ok "28 requests under $sh: the answers of the experiment (§17 B)"; else bad "gate grammar under $sh: $n wrong" "$out"; fi
done
mkgate "$ROOT/gate-nochar" '/bad-character "allowed/d'
out="$(gate_suite sh "$ROOT/gate-nochar")"; n=$?
if [ "$n" -gt 0 ]; then ok "must-fail: with the character check removed, $n request(s) get through (as they must)"
else bad "must-fail: the gate without its character check still refused everything"; fi
# the key's name comes from $SSH_USER_AUTH and the root-owned key file, never from the client
printf 'publickey ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEYKEY\n' > "$ROOT/auth1"
eq "the key's name is the key file's comment for the key that logged in" "$(SSH_USER_AUTH="$ROOT/auth1" SSH_ORIGINAL_COMMAND=hello sh "$ROOT/gate")" \
  '{"ok":true,"args":"node serve --key=avi-mac hello"}'
printf 'publickey ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOTHEROTHEROTHEROTHEROTHEROTHEROTHEROTHER\n' > "$ROOT/auth2"
eq "a name outside [a-z0-9-] becomes unknown" "$(SSH_USER_AUTH="$ROOT/auth2" SSH_ORIGINAL_COMMAND=hello sh "$ROOT/gate")" \
  '{"ok":true,"args":"node serve --key=unknown hello"}'
# a key that is no longer in the key file (revoked) gets nothing, also over a connection opened before the revoke
printf 'publickey ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIREVOKEDREVOKEDREVOKEDREVOKEDREVOKEDREVOK\n' > "$ROOT/auth3"
has "a key not in the key file (revoked) is refused: key-revoked" "$(SSH_USER_AUTH="$ROOT/auth3" SSH_ORIGINAL_COMMAND=hello sh "$ROOT/gate")" \
  '"error":"key-revoked"'
mkgate "$ROOT/gate-norevoke" '/mu-gate: revoke check/d'
case "$(SSH_USER_AUTH="$ROOT/auth3" SSH_ORIGINAL_COMMAND=hello sh "$ROOT/gate-norevoke")" in
  *'"ok":true'*) ok "must-fail: with the revoke check removed, the revoked key's request gets through (as it must)" ;;
  *) bad "must-fail: the gate without its revoke check still refused the revoked key" ;;
esac
sed -e 's|@MU_BIN@|x|' "$REPO/install/hosts/wsl2/mu-node-gate" > "$ROOT/gate-half"
has "a gate whose paths were not written in refuses everything" "$(SSH_ORIGINAL_COMMAND=hello sh "$ROOT/gate-half")" '"error":"not-installed"'

# ---------------------------------------------------------------- dispatcher routing
section "dispatcher routing (bash $(/bin/bash -c 'echo $BASH_VERSION')): job commands always run the default toolchain"
stub_version() {   # stub_version V WITH_JOBS: an installed stub version; WITH_JOBS=1 gives it this checkout's src/mu
  local T="$MU_HOME/toolchains/$1"
  mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/libexec"
  printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "2026-11-14 10:00:00",\n "layers": []\n}\n' "$1" > "$T/MANIFEST.json"
  printf '#!/bin/bash\n# stub toolchain %s\ncase "$1" in run) shift; exec "$@" ;; *) echo "stub toolchain %s: $*"; exit 0 ;; esac\n' "$1" "$1" > "$T/recipe/bin/mu-toolchain"
  chmod +x "$T/recipe/bin/mu-toolchain"
  ln -sf "$PY1" "$T/env/bin/python"
  if [ "$2" = 1 ]; then mkdir -p "$T/recipe/src"; cp -R "$REPO/src/mu" "$T/recipe/src/mu"; fi
}
mkdir -p "$MU_HOME/bin"; install -m 755 "$REPO/bin/mu" "$MU_HOME/bin/mu"
stub_version v2026.11.6 0; stub_version v2026.11.7 1; echo v2026.11.7 > "$MU_HOME/default"
mu() { /bin/bash "$MU_HOME/bin/mu" "$@"; }
mkdir -p "$ROOT/old project"; printf '[mu]\nversion = "v2026.11.6"\n' > "$ROOT/old project/mu.toml"
out="$(cd "$ROOT/old project" && mu compute list --json 2>&1)"; rc=$?
eq "in a project pinned to v2026.11.6 (no job layer), mu compute list works (exit 0)" "$rc" 0
has "  and answers JSON" "$out" '"ok": true'
out="$(cd "$ROOT/old project" && MU_DISPATCH_FAULT=route-by-pin mu compute list --json 2>&1)"; rc=$?
if [ "$rc" = 3 ]; then ok "must-fail: routed by the project's pin (fault route-by-pin), it fails with exit 3 (as it must)"
else bad "must-fail: route-by-pin did not fail" "rc=$rc $out"; fi
out="$(cd "$ROOT/cwd" && MU_VERSION=v2026.11.6 mu compute list --json 2>&1)"; rc=$?
eq "MU_VERSION=v2026.11.6 does not steer a job command either" "$rc" 0
out="$(cd "$ROOT/old project" && mu run sh -c 'echo $MU_VERSION' 2>&1)"
eq "other commands still follow the pin (mu run under the stub v2026.11.6)" "$out" ""
echo v2026.11.6 > "$MU_HOME/default"
out="$(cd "$ROOT/cwd" && mu compute list 2>&1)"; rc=$?
eq "a default without the job layer: exit 3" "$rc" 3
has "  and says to update" "$out" "Run: mu update"
echo v2026.11.7 > "$MU_HOME/default"
out="$(cd "$ROOT/cwd" && mu status 2>&1)"; rc=$?
eq "mu status outside a project: exit 2" "$rc" 2
has "  says it is not a project" "$out" "not a μ project"
out="$(cd "$ROOT/cwd" && mu status 20261114-101500-k3f9qa --json 2>&1)"; rc=$?
eq "mu status <job> with no node paired: exit 9" "$rc" 9
has "  --json carries the exit code and what to do next" "$out" '"next": "pair one: mu compute invite'
out="$(cd "$ROOT/cwd" && mu submit --on local -- true 2>&1)"; rc=$?
eq "mu submit --on local: exit 2 (stage 2)" "$rc" 2
out="$(cd "$ROOT/cwd" && mu log not-an-id 2>&1)"; rc=$?
eq "a malformed job id: exit 2" "$rc" 2
has "mu help lists the job commands" "$(mu help 2>&1)" "mu submit [--on NODE]"
t0="$(date +%s)"; for i in 1 2 3 4 5; do mu compute list --json >/dev/null 2>&1; done; t1="$(date +%s)"
echo "  info  5 starts of a job command: $((t1 - t0)) s"

# ---------------------------------------------------------------- python units
for p in $PYS; do
  section "unit checks, $p"
  for s in durable ids globs proxy classify sshconfig controlpath unpack untrusted-node runner snapshot-alone snapshot-content snapshot-report; do
    py_section "$p" "$s"
  done
  py_section "$p" atomic "$([ "${QUICK:-0}" = 1 ] && echo 30 || echo 120)"
done
section "the same checks, each with its fault put in"
must_fail "torn records (a changed record with the old sum)" no-checksum "$PY1" durable
must_fail "atomic write" in-place "$PY1" atomic 120
must_fail "ssh_config as specified (host key pin)" no-strict-hostkey "$PY1" sshconfig
must_fail "fetch stays inside" no-member-check "$PY1" unpack
must_fail "fetch integrity" no-fetch-hash "$PY1" unpack
must_fail "snapshot leaves the project alone" user-index "$PY1" snapshot-alone
must_fail "snapshot content (.mu/ left out)" no-mu-exclude "$PY1" snapshot-content
# the review's findings (docs/design/job-layer.md §23), each check with the fault that undoes its fix
must_fail "the workstation's glob is never wider than the node's" dot-globs "$PY1" globs
must_fail "fetch never writes into .git or .mu" dot-globs "$PY1" unpack
must_fail "fetch stops a member longer than the result says" no-size-guard "$PY1" unpack
must_fail "a node's refusal cannot set next, exit or crash the client" node-hint "$PY1" classify
must_fail "mu fetch takes the declared outputs from this clone, not the node" trust-node-globs "$PY1" untrusted-node
must_fail "mu fetch: a node's refusal through the real CLI" node-hint "$PY1" untrusted-node
must_fail "runner: a failed systemctl show is not the end of a job" bus-error-is-end "$PY1" runner
must_fail "runner: a failed systemd-run is checked with systemd" trust-run-rc "$PY1" runner
must_fail "runner: an unreadable state.json is rebuilt" no-unit-check-torn "$PY1" runner
must_fail "runner: a re-run keeps the log" log-truncate-on-rerun "$PY1" runner
must_fail "runner: one job's error does not stop the pass" runner-pass-aborts "$PY1" runner
must_fail "runner: no start below the free-disk minimum" no-start-disk-check "$PY1" runner
must_fail "runner: the log cap" no-log-cap "$PY1" runner
must_fail "runner: a damaged job folder is kept" delete-damaged "$PY1" runner
must_fail "runner: results are written once" overwrite-result "$PY1" runner

section "chisel pins agree (apps.toml, relay/Dockerfile, the distro script)"
py_section "$PY1" pins "$REPO"
mkdir -p "$ROOT/pins/toolchain" "$ROOT/pins/relay" "$ROOT/pins/install/hosts/wsl2"
cp "$REPO/install/hosts/wsl2/mu-wsl2-distro.sh" "$ROOT/pins/install/hosts/wsl2/"
sed 's/f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6/f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe7/' \
  "$REPO/relay/Dockerfile" > "$ROOT/pins/relay/Dockerfile"
cp "$REPO/toolchain/apps.toml" "$ROOT/pins/toolchain/"
out="$("$PY1" -I -S "$HERE/unit.py" pins "$ROOT/pins" 2>&1)"
if printf '%s\n' "$out" | grep -q '^FAIL '; then ok "must-fail: with one sum changed in relay/Dockerfile, the pin check fails (as it must)"
else bad "must-fail: a changed sum was not noticed" "$out"; fi
cp "$REPO/relay/Dockerfile" "$ROOT/pins/relay/Dockerfile"
sed 's/^CHISEL_GZ_SHA256="f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"/CHISEL_GZ_SHA256="f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe7"/' \
  "$REPO/install/hosts/wsl2/mu-wsl2-distro.sh" > "$ROOT/pins/install/hosts/wsl2/mu-wsl2-distro.sh"
out="$("$PY1" -I -S "$HERE/unit.py" pins "$ROOT/pins" 2>&1)"
if printf '%s\n' "$out" | grep -q '^FAIL '; then ok "must-fail: with the sum changed in the distro script, the pin check fails (as it must)"
else bad "must-fail: a changed sum in the distro script was not noticed" "$out"; fi

# ---------------------------------------------------------------- mu compute add / list / remove
section "mu compute add / list / remove (no network: a stub for /usr/bin/security)"
cat > "$STUBS/security" <<'S'
#!/bin/bash
# stands in for /usr/bin/security: logs its argument list (never stdin), keeps items in $SEC_STORE
printf '%s\n' "$*" >> "$SEC_LOG"
mkdir -p "$SEC_STORE"
case "$1" in
  -i) IFS= read -r line; acct="$(printf '%s' "$line" | sed -n 's/.* -a "\([^"]*\)".*/\1/p' | tr '/' '_')"
      pw="$(printf '%s' "$line" | sed -n 's/.* -w "\([^"]*\)".*/\1/p')"; printf '%s' "$pw" > "$SEC_STORE/$acct" ;;
  find-generic-password) acct="$(printf '%s' "$3" | tr '/' '_')"
      [ -f "$SEC_STORE/$acct" ] || { echo "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain." >&2; exit 44; }
      [ ! -e "$SEC_STORE/LOCKED" ] || sleep 60
      cat "$SEC_STORE/$acct"; echo ;;
  delete-generic-password) rm -f "$SEC_STORE/$(printf '%s' "$3" | tr '/' '_')" ;;
esac
S
chmod +x "$STUBS/security"
export MU_SECURITY="$STUBS/security" SEC_LOG="$ROOT/security.log" SEC_STORE="$ROOT/keychain"
HOSTKEY_PUB="$(ssh-keygen -q -t ed25519 -N '' -f "$ROOT/hostkey" -C node >/dev/null && cut -d' ' -f1,2 "$ROOT/hostkey.pub")"
HOSTKEY_FP="$(ssh-keygen -l -f "$ROOT/hostkey.pub" | awk '{print $2}')"
PW="pAsSwOrD0123456789abcdef_-XYZ"
FP="ubILCH8awo2GQII9Z9eLRD+LOo4qXabtMrJjCleonuI="
add() { printf '%s\n' "$PW" | mu compute add "$@" --relay https://relay.example.invalid --relay-fingerprint "$FP" --relay-user avi-mac --relay-password-stdin; }
out="$(cd "$ROOT/cwd" && add office-pc --host-key "$HOSTKEY_PUB" --secret-store keychain --json 2>&1)"; rc=$?
eq "mu compute add (Keychain): exit 0" "$rc" 0
has "  prints the host key's fingerprint as ssh-keygen gives it" "$out" "\"host_key_fingerprint\": \"$HOSTKEY_FP\""
has "  prints the one command for the PC" "$out" "mu-wsl2-distro authorize avi-mac 'ssh-ed25519 "
D="$MU_HOME/compute/office-pc"
eq "  the folder is 0700" "$(ls -ld "$D" | cut -c1-10)" "drwx------"
eq "  known_hosts pins the host key under the alias" "$(cat "$D/known_hosts")" "mu-office-pc $HOSTKEY_PUB"
eq "  the key is 0600" "$(ls -l "$D/id_ed25519" | cut -c1-10)" "-rw-------"
eq "  the Keychain item holds the password" "$(cat "$SEC_STORE/relay.example.invalid_avi-mac" 2>/dev/null)" "$PW"
hasnt "  the password is in no argument list of security" "$(cat "$SEC_LOG")" "$PW"
has "  it was written with security -i" "$(head -1 "$SEC_LOG")" "-i"
[ ! -e "$D/relay.auth" ] && ok "  no relay.auth file with the Keychain" || bad "  relay.auth exists with the Keychain"
diff <("$PY1" -I -S -c 'import sys; sys.path.insert(0, sys.argv[1]); from mu import jobs; sys.stdout.write(jobs.ssh_config_text("office-pc", sys.argv[2]))' "$REPO/src" "$MU_HOME") "$D/ssh_config" >/dev/null \
  && ok "  ssh_config is the generated text" || bad "  ssh_config differs"
out="$(cd "$ROOT/cwd" && add office-pc --host-key "$HOSTKEY_PUB" --secret-store keychain 2>&1)"; rc=$?
eq "a second add of the same node: exit 2" "$rc" 2
has "  says to remove it first" "$out" "mu compute remove office-pc first"
out="$(cd "$ROOT/cwd" && add other --host-key "$HOSTKEY_PUB
$HOSTKEY_PUB" 2>&1)"; rc=$?
eq "two host keys: exit 2" "$rc" 2
out="$(cd "$ROOT/cwd" && add other --host-key "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ" 2>&1)"; rc=$?
eq "an RSA host key: exit 2" "$rc" 2
out="$(cd "$ROOT/cwd" && printf 'short\n' | mu compute add other --host-key "$HOSTKEY_PUB" --relay https://relay.example.invalid --relay-fingerprint "$FP" --relay-user avi-mac --relay-password-stdin 2>&1)"; rc=$?
eq "a password outside [A-Za-z0-9_-]{24,}: exit 2" "$rc" 2
out="$(cd "$ROOT/cwd" && add local --host-key "$HOSTKEY_PUB" 2>&1)"; rc=$?
eq "the reserved name local: exit 2" "$rc" 2
[ ! -e "$MU_HOME/compute/other" ] && ok "refused adds leave no folder" || bad "a refused add left a folder"
out="$(cd "$ROOT/cwd" && add filepc --host-key "$HOSTKEY_PUB" --secret-store file --json 2>&1)"; rc=$?
eq "mu compute add --secret-store file: exit 0" "$rc" 0
eq "  relay.auth is user:password, 0600" "$(ls -l "$MU_HOME/compute/filepc/relay.auth" | cut -c1-10) $(cat "$MU_HOME/compute/filepc/relay.auth")" "-rw------- avi-mac:$PW"
out="$(cd "$ROOT/cwd" && mu compute list --json 2>&1)"
has "mu compute list shows both" "$out" '"node": "filepc"'
out="$(cd "$ROOT/cwd" && mu submit -- true 2>&1)"; rc=$?
eq "mu submit with two nodes and no --on: exit 2" "$rc" 2
# the proxy reads the password the way it will at a connection: from the Keychain, within 10 s
: > "$SEC_LOG"
out="$(cd "$ROOT/cwd" && MU_CHISEL=/nonexistent/chisel MU_PROXY_STATUS="$ROOT/st" sh -c ': > "$MU_PROXY_STATUS"; "$0" compute proxy office-pc < /dev/null' "$MU_HOME/bin/mu" 2>&1)"; rc=$?
has "the proxy reads the Keychain item (then fails here: no chisel)" "$(cat "$SEC_LOG")" "find-generic-password -a relay.example.invalid/avi-mac -s systems.martand.mu.relay -w"
touch "$SEC_STORE/LOCKED"; : > "$ROOT/st"
t0="$(date +%s)"
(cd "$ROOT/cwd" && MU_PROXY_STATUS="$ROOT/st" /bin/bash "$MU_HOME/bin/mu" compute proxy office-pc < /dev/null > /dev/null 2>&1); rc=$?
t1="$(date +%s)"; rm -f "$SEC_STORE/LOCKED"
eq "a locked keychain: the proxy gives up (exit 1)" "$rc" 1
[ $((t1 - t0)) -le 13 ] && ok "  within the 10 s limit ($((t1 - t0)) s)" || bad "  took $((t1 - t0)) s"
has "  and the status file says why" "$(cat "$ROOT/st")" "keychain-locked"
pkill -f "$STUBS/security" 2>/dev/null || true
out="$(cd "$ROOT/cwd" && mu compute remove office-pc --json 2>&1)"; rc=$?
eq "mu compute remove: exit 0" "$rc" 0
has "  prints the revoke command" "$out" "mu-wsl2-distro revoke avi-mac"
[ ! -e "$D" ] && ok "  the folder is gone" || bad "  the folder is still there"
[ ! -e "$SEC_STORE/relay.example.invalid_avi-mac" ] && ok "  the Keychain item is gone" || bad "  the Keychain item is still there"
out="$(cd "$ROOT/cwd" && mu compute status nope --json 2>&1)"; rc=$?
eq "mu compute status of an unknown node: exit 9" "$rc" 9

# ---------------------------------------------------------------- summary
printf '\n%d passed, %d failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
exit 0
