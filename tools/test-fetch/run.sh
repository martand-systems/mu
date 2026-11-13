#!/bin/bash
# Tests for the installer's downloads (install/download.sh: fetch) against a local fault-injecting server.
#
#   tools/test-fetch/run.sh [BASH ...]      default: /bin/bash plus any other bash found (e.g. Homebrew's bash 5)
#   CASES="basic a-drop-single" tools/test-fetch/run.sh     run only some cases (names as printed)
#
# Needs python3 (stdlib) and curl. Uses ~170 MB in a temporary folder (an 80 MB and a 3 MB random file, served and
# downloaded), deleted afterwards. Exit status 0 when every case passes under every bash.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
TF="$(mktemp -d "${TMPDIR:-/tmp}/mu-test-fetch.XXXXXX")"
SRV_PID=""
cleanup() { [ -z "$SRV_PID" ] || kill "$SRV_PID" 2>/dev/null || true; rm -rf "$TF"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ $# -gt 0 ]; then BASHES="$*"; else
  BASHES="/bin/bash"; seen="$(/bin/bash -c 'echo $BASH_VERSION')"
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash $(command -v -a bash 2>/dev/null || true); do
    [ -x "$b" ] || continue; v="$("$b" -c 'echo $BASH_VERSION')"
    case " $seen " in *" $v "*) ;; *) BASHES="$BASHES $b"; seen="$seen $v" ;; esac
  done
fi

mkdir -p "$TF/srv"
python3 -I "$HERE/server.py" "$TF/srv" "$TF/port" 80 3 > "$TF/server.log" 2>&1 &
SRV_PID=$!
for _ in $(seq 1 300); do [ -s "$TF/port" ] && break; sleep 0.1; done
[ -s "$TF/port" ] || { cat "$TF/server.log"; echo "server did not start" >&2; exit 1; }
export REPO TF PORT="$(cat "$TF/port")"
export BIG_SHA="$(shasum -a 256 "$TF/srv/big.bin" | cut -d' ' -f1)" SMALL_SHA="$(shasum -a 256 "$TF/srv/small.bin" | cut -d' ' -f1)"
export BIG_SIZE="$(wc -c < "$TF/srv/big.bin" | tr -d ' ')"
echo "test server on 127.0.0.1:$PORT; big.bin $BIG_SIZE bytes; small.bin $(wc -c < "$TF/srv/small.bin" | tr -d ' ') bytes"

rc=0
for b in $BASHES; do
  "$b" "$HERE/cases.sh" | tee "$TF/out.txt" || rc=1
  grep -q '^FAIL' "$TF/out.txt" && rc=1
done
echo
if [ "$rc" = 0 ]; then echo "all cases passed"; else echo "SOME CASES FAILED"; fi
exit "$rc"
