# shellcheck shell=bash
# μ installer: downloads. Sourced by install/install.sh (and by tools/test-fetch/cases.sh). Runs under macOS /bin/bash
# 3.2 and bash 5 with the installer's `set -Eeuo pipefail` and ERR trap active: every command that may fail runs
# inside _dl_main, a subshell that drops the ERR trap and errexit and reports one result line.
#
#   fetch NAME URL SHA256   prints the cached path (and nothing else on stdout); messages go to stderr. Uses MU_CACHE,
#                           SEED (a folder of archives; may be empty) and fail MSG (aborts the install) from the caller.
#   sha256 FILE · sha256_stdin · net_hint URL
#
# A download never throws progress away. Every attempt resumes where the previous one stopped, and attempts continue
# as long as they make progress (MU_DL_MAX_IDLE attempts in a row without a new byte end it, with backoff up to
# MU_DL_BACKOFF_MAX s; MU_DL_MAX_ATTEMPTS bounds the total). A connection that stops delivering (under 1 KB/s for
# MU_DL_STALL_S s) is cut and resumed instead of waiting for a TCP timeout. Partial data is discarded only when the
# server cannot resume (curl 33, HTTP 416, a 200 answer to a range request) or the finished file fails its SHA-256.
# curl's own --retry is not used for the transfers: when it retries, curl truncates the output back to where that curl
# run started, so a run that fails late loses everything it downloaded (the 2026-11 WSL2 ParaView failure).
#
# A large file (MU_DL_SEG_MIN bytes or more, default 64 MiB) from a server that serves byte ranges downloads as
# MU_DL_SEGMENTS ranges in parallel (default 8; 1 disables), each resumed on its own, then joined in order; the SHA-256
# of the joined file is the integrity check. The URL is resolved once (redirects followed), so every range uses the same
# mirror; if a range gets 403/410 later (a signed redirect URL expired), the original URL is resolved again.
#
# State in $MU_CACHE/downloads, kept across runs so that a re-run resumes:
#   NAME.part            one connection's partial file, or the joined file before its SHA-256 check
#   NAME.part.NN         range NN (00, 01, ...); NAME.part.layout: total size, then "start end" per range
#   NAME.part.url        the resolved URL; NAME.part.hdr, NAME.part.NN.hdr: response headers of the last attempt

if command -v sha256sum >/dev/null 2>&1; then sha256() { sha256sum "$1" | cut -d' ' -f1; }; sha256_stdin() { sha256sum | cut -d' ' -f1; }
else sha256() { /usr/bin/openssl dgst -sha256 -r "$1" | cut -d' ' -f1; }; sha256_stdin() { /usr/bin/openssl dgst -sha256 -r | cut -d' ' -f1; }; fi

# Explain a failed download in plain words (captive portals and HTTPS-intercepting proxies are the usual cause)
net_hint() {
  local out; out="$(curl -sSI --max-time 10 "$1" 2>&1 >/dev/null || true)"
  case "$out" in
    *certificate*|*SSL*|*ssl*) echo "the network is intercepting HTTPS (captive portal, proxy or filtering VPN): sign in to the network or switch networks, then re-run" ;;
    *resolve*) echo "no internet connection (DNS lookup failed)" ;;
    *timed\ out*|*Timeout*) echo "the connection timed out (offline or very slow network)" ;;
    *) echo "${out:-download failed}" ;;
  esac
}

# fetch NAME URL SHA256 -> prints the cached path. Downloads once (resumable), verifies SHA-256, can reuse a seed folder.
fetch() {
  local name="$1" url="$2" sha="$3" dest="$MU_CACHE/downloads/$1" res
  if [ -f "$dest" ] && [ "$(sha256 "$dest")" = "$sha" ]; then echo "$dest"; return 0; fi
  if [ -n "${SEED:-}" ] && [ -f "$SEED/$name" ] && [ "$(sha256 "$SEED/$name")" = "$sha" ]; then
    cp "$SEED/$name" "$dest"; echo "$dest"; return 0
  fi
  echo "downloading $name" >&2
  # one result line: ok | sha <got> | fail | stop ("|| true": a signal that ends it must not trip the ERR trap)
  res="$(_dl_main "$name" "$url" "$sha" "$dest" || true)"
  case "$res" in
    ok) echo "$dest" ;;
    sha\ *) fail "SHA-256 mismatch for $name: got ${res#sha }, expected $sha" ;;
    stop) fail "the download of $name was interrupted" ;;
    *) fail "download failed: $url ($(net_hint "$url"))" ;;
  esac
}

# ---------------------------------------------------------------- internals (run inside _dl_main only)
if stat -c %s /dev/null >/dev/null 2>&1; then _dl_size() { if [ -f "$1" ]; then stat -c %s "$1"; else echo 0; fi; }
else _dl_size() { if [ -f "$1" ]; then stat -f %z "$1"; else echo 0; fi; }; fi
_dl_int() { case "$1" in ''|*[!0-9]*) echo "$2" ;; *) echo "$1" ;; esac; }   # _dl_int VALUE DEFAULT
_dl_mb() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1048576 }'; }
_dl_code() { tr -d '\r' < "$1" 2>/dev/null | awk '/^HTTP\//{ c = $2 } END { print c + 0 }'; }   # last status in a header dump
# first byte of the last Content-Range in a header dump (-1 if there is none)
_dl_crstart() { tr -d '\r' < "$1" 2>/dev/null | awk '/^HTTP\//{ s = -1 } tolower($1) == "content-range:" { split($3, a, "-"); s = a[1] + 0 } END { print (s == "" ? -1 : s) }'; }
_dl_truncate() { dd if=/dev/null of="$1" bs=1 seek="$2" 2>/dev/null; }                        # _dl_truncate FILE SIZE
_dl_backoff() { local d=1 i=1; while [ "$i" -lt "$1" ] && [ "$d" -lt "$_DL_BMAX" ]; do d=$((d * 2)); i=$((i + 1)); done
  [ "$d" -le "$_DL_BMAX" ] || d=$_DL_BMAX; echo "$d"; }
_dl_kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do _dl_kill_tree "$c"; done; kill "$1" 2>/dev/null; }
_dl_stop() { local p; for p in $_DL_PIDS; do _dl_kill_tree "$p"; done; _DL_PIDS=""; }
# every transfer: fail on HTTP errors, follow redirects, cut a connection that delivers under 1 KB/s for _DL_STALL s
_dl_curl() { curl -sS -L -f --connect-timeout 20 --speed-limit 1024 --speed-time "$_DL_STALL" "$@" 6>&-; }
_dl_rm_aux() { rm -f "$_DL_PART".[0-9][0-9] "$_DL_PART".[0-9][0-9].hdr "$_DL_PART.hdr" "$_DL_PART.url" "$_DL_PART".url.* "$_DL_PART.layout.tmp"; }
_dl_discard() { rm -f "$_DL_PART" "$_DL_PART.layout"; _dl_rm_aux; }
# leave the ranged mode: range 00 starts at byte 0, so it is a correct prefix and becomes the single-connection file
_dl_collapse() {
  [ -f "$_DL_PART.layout" ] || return 0
  if [ -f "$_DL_PART.00" ]; then rm -f "$_DL_PART"; mv -f "$_DL_PART.00" "$_DL_PART"; fi
  rm -f "$_DL_PART.layout"; _dl_rm_aux
}

# _dl_probe URL HEADERS -> "curl-rc http-code total-size|none effective-url". A GET of byte 0 (not a HEAD: signed
# redirect targets often refuse HEAD): a 206 with Content-Range proves range support and gives the size. A server that
# ignores the range answers 200 with the whole file, which --max-filesize 1 stops before the body (curl 63).
_dl_probe() {
  local out rc code eff total
  out="$(curl -sS -L --connect-timeout 20 --max-time 30 -r 0-0 --max-filesize 1 -o /dev/null -D "$2" \
         -w '%{http_code} %{url_effective}' "$1" 2>/dev/null 6>&-)"; rc=$?
  code="${out%% *}"; eff="${out#* }"; [ -n "$code" ] || code=000; [ "$eff" != "$out" ] || eff=""
  total="$(tr -d '\r' < "$2" 2>/dev/null | awk '/^HTTP\//{ t = "" } tolower($1) == "content-range:" { n = split($0, a, "/"); t = a[n] } END { print t }')"
  case "$total" in ''|*[!0-9]*) total=none ;; esac
  echo "$rc $code $total ${eff:-$1}"
}

# _dl_resolve NN OLD-URL: point the ranges at a fresh redirect target of the original URL (a signed URL expired).
# Returns 0 if the URL changed.
_dl_resolve() {
  local nn="$1" old="$2" rc code total eff
  [ "$(cat "$_DL_PART.url" 2>/dev/null)" = "$old" ] || return 0   # another range already resolved it
  read -r rc code total eff <<< "$(_dl_probe "$_DL_URL" "$_DL_PART.$nn.hdr")"
  [ "$code" = 206 ] || eff="$_DL_URL"
  printf '%s\n' "$eff" > "$_DL_PART.url.$nn" && mv -f "$_DL_PART.url.$nn" "$_DL_PART.url"
  [ "$eff" != "$old" ] || return 1
  echo "  $_DL_NAME: range $((10#$nn + 1)): HTTP 403/410: resolved the URL again"
}

# _dl_single: one connection into $_DL_PART, resumed with curl -C - after every failure. 0 done, 1 failed, 130 stopped.
_dl_single() {
  local have after code rc idle=0 tries=0 restarts=0 delay
  while :; do
    have=$(_dl_size "$_DL_PART")
    code="$(_dl_curl -C - -o "$_DL_PART" -w '%{http_code}' "$_DL_URL")"; rc=$?
    [ "$rc" -lt 128 ] || return 130
    [ "$rc" != 0 ] || return 0   # (a 416 to a resume of a complete file also ends here; the SHA-256 decides)
    after=$(_dl_size "$_DL_PART")
    if [ "$rc" = 33 ] || [ "$code" = 416 ]; then   # the server cannot resume this file: start over (rarely, bounded)
      restarts=$((restarts + 1))
      [ "$restarts" -le 3 ] || { echo "  $_DL_NAME: the server cannot resume and the download keeps failing"; return 1; }
      echo "  $_DL_NAME: the server cannot resume (curl $rc, HTTP $code): starting over from 0 MB"
      rm -f "$_DL_PART"; _DL_OLD=0; continue
    fi
    case "$code" in 408|429) ;; 4??) echo "  $_DL_NAME: HTTP $code"; return 1 ;; esac   # permanent (404, 403, ...)
    tries=$((tries + 1))
    if [ "$after" -gt "$have" ]; then idle=0; delay=1; else idle=$((idle + 1)); delay=$(_dl_backoff "$idle"); fi
    if [ "$idle" -ge "$_DL_IDLE" ] || [ "$tries" -ge "$_DL_TRIES" ]; then
      echo "  $_DL_NAME: giving up after $tries attempts (curl $rc, HTTP $code) at $(_dl_mb "$after") MB"; return 1
    fi
    echo "  $_DL_NAME: stopped at $(_dl_mb "$after") MB (curl $rc, HTTP $code); resuming in ${delay} s (attempt $((tries + 1)))"
    sleep "$delay" 6>&- || return 130
  done
}

# _dl_worker NN START END: appends bytes START..END-1 of the file to $_DL_PART.NN, resuming after failures.
# Exits 0 done, 1 gave up, 3 the server did not honour the range (nothing of that answer is kept), 130 stopped.
_dl_worker() {
  local nn="$1" seg="$_DL_PART.$1" hdr="$_DL_PART.$1.hdr" want=$(($3 - $2)) have after code rc url idle=0 tries=0 delay
  local label="range $((10#$1 + 1))/$_DL_N"
  while :; do
    have=$(_dl_size "$seg")
    [ "$have" -lt "$want" ] || break
    url="$(cat "$_DL_PART.url" 2>/dev/null)"; [ -n "$url" ] || url="$_DL_URL"
    # --max-filesize: an answer longer than the range asked for (a 200 with the whole file) stops before its body
    _dl_curl -r "$(($2 + have))-$(($3 - 1))" --max-filesize "$((want - have))" -D "$hdr" "$url" >> "$seg"; rc=$?
    [ "$rc" -lt 128 ] || exit 130
    code=$(_dl_code "$hdr"); after=$(_dl_size "$seg")
    # an answer that is not the range asked for (whole file, refused, or another range): keep nothing of it
    if [ "$rc" = 63 ] || [ "$code" = 200 ] || [ "$code" = 416 ] \
       || { [ "$code" = 206 ] && [ "$(_dl_crstart "$hdr")" != $(($2 + have)) ]; }; then
      [ "$after" = "$have" ] || _dl_truncate "$seg" "$have"
      echo "  $_DL_NAME: $label was answered with HTTP $code (curl $rc): the server does not serve ranges reliably"
      exit 3
    fi
    if [ "$after" -gt "$want" ]; then echo "  $_DL_NAME: $label is too long; downloading it again"; rm -f "$seg"; after=0; fi
    [ "$after" -lt "$want" ] || break
    # a short but successful answer (some servers cap range lengths): ask for the rest at once
    if [ "$rc" = 0 ] && [ "$after" -gt "$have" ]; then continue; fi
    tries=$((tries + 1))
    # an expired signed URL: a fresh one is worth an immediate retry (bounded by the total attempts)
    if { [ "$code" = 403 ] || [ "$code" = 410 ]; } && [ "$tries" -lt "$_DL_TRIES" ] && _dl_resolve "$nn" "$url"; then continue; fi
    if [ "$after" -gt "$have" ]; then idle=0; delay=1; else idle=$((idle + 1)); delay=$(_dl_backoff "$idle"); fi
    if [ "$idle" -ge "$_DL_IDLE" ] || [ "$tries" -ge "$_DL_TRIES" ]; then
      echo "  $_DL_NAME: $label: giving up after $tries attempts (curl $rc, HTTP $code)"; exit 1
    fi
    echo "  $_DL_NAME: $label stopped at $(_dl_mb "$after") of $(_dl_mb "$want") MB (curl $rc, HTTP $code); resuming in ${delay} s (attempt $((tries + 1)))"
    sleep "$delay" || exit 130
  done
  rm -f "$hdr"; exit 0
}

# _dl_layout TOTAL N: plan N ranges (fewer for a small remainder). A partial single-connection file is a correct
# prefix: it is kept as the start of range 00, and the rest of the file is split. Returns 1 if ranges are not worth it.
_dl_layout() {
  local total="$1" n="$2" base rem chunk i s e
  base=$(_dl_size "$_DL_PART")
  [ "$base" -lt "$total" ] || return 1   # complete (or too long): one resume request settles it
  rem=$((total - base)); [ "$n" -le $((rem / 4194304)) ] || n=$((rem / 4194304))
  [ "$n" -ge 2 ] || return 1
  chunk=$((rem / n))
  { echo "$total"; i=0
    while [ "$i" -lt "$n" ]; do
      s=$((base + i * chunk)); e=$((base + (i + 1) * chunk)); [ "$i" != 0 ] || s=0; [ "$i" != $((n - 1)) ] || e=$total
      echo "$s $e"; i=$((i + 1))
    done; } > "$_DL_PART.layout.tmp" || return 1
  mv -f "$_DL_PART.layout.tmp" "$_DL_PART.layout" || return 1
  [ "$base" = 0 ] || echo "  $_DL_NAME: $(_dl_mb "$base") MB from an earlier attempt kept"
  return 0
}

# _dl_ranges: download the ranges of $_DL_PART.layout in parallel and join them into $_DL_PART.
# 0 joined, 1 failed (the ranges stay for the next run), 3 the server stopped serving ranges, 130 stopped.
_dl_ranges() {
  local p rc st=0 i n seg total
  # a partial single-connection file adopted by the layout becomes range 00 (it starts at byte 0)
  if [ -f "$_DL_PART" ]; then if [ -f "$_DL_PART.00" ]; then rm -f "$_DL_PART"; else mv -f "$_DL_PART" "$_DL_PART.00"; fi; fi
  total=$(head -1 "$_DL_PART.layout")
  set -- $(sed 1d "$_DL_PART.layout"); _DL_N=$(($# / 2)); i=0
  echo "  $_DL_NAME: $(_dl_mb "$total") MB in $_DL_N parallel ranges from $(sed -e 's|^[a-z]*://||' -e 's|[/?].*||' "$_DL_PART.url" 2>/dev/null)"
  while [ $# -ge 2 ]; do
    ( exec 6>&-; _dl_worker "$(printf '%02d' "$i")" "$1" "$2" ) &
    _DL_PIDS="$_DL_PIDS $!"; i=$((i + 1)); shift 2
  done
  for p in $_DL_PIDS; do
    wait "$p"; rc=$?
    case "$rc" in 0) ;; 130) st=130 ;; 3) [ "$st" = 130 ] || st=3 ;; *) [ "$st" != 0 ] || st=1 ;; esac
  done
  _DL_PIDS=""
  [ "$st" = 0 ] || return "$st"
  # every range must have exactly its length before the join
  set -- $(sed 1d "$_DL_PART.layout"); i=0
  while [ $# -ge 2 ]; do
    seg="$_DL_PART.$(printf '%02d' "$i")"
    [ "$(_dl_size "$seg")" = $(($2 - $1)) ] || { echo "  $_DL_NAME: range $((i + 1)) has the wrong size"; rm -f "$seg"; return 1; }
    i=$((i + 1)); shift 2
  done
  # join in order. The layout goes first: from then on $_DL_PART is always a correct prefix of the file, so an
  # interrupted join resumes on one connection (the leftover ranges are dropped)
  rm -f "$_DL_PART.layout"; mv -f "$_DL_PART.00" "$_DL_PART" || return 1
  n=$i; i=1
  while [ "$i" -lt "$n" ]; do
    seg="$_DL_PART.$(printf '%02d' "$i")"; cat "$seg" >> "$_DL_PART" || return 1; rm -f "$seg"; i=$((i + 1))
  done
  _dl_rm_aux; return 0
}

# _dl_ranges_or_single: ranges; if the server turns out not to honour them, continue on one connection
_dl_ranges_or_single() {
  local rc; _dl_ranges; rc=$?
  [ "$rc" = 3 ] || return "$rc"
  echo "  $_DL_NAME: continuing on one connection"; _dl_collapse; _dl_single
}

# _dl_get: bring $_DL_PART to the complete file (not yet verified). 0 done, 1 failed, 130 stopped.
_dl_get() {
  local n rc code total eff ltotal
  n=$(_dl_int "${MU_DL_SEGMENTS:-}" 8); [ "$n" -le 16 ] || n=16
  # range files without a layout are left from an interrupted join: $_DL_PART already holds the joined prefix
  [ -f "$_DL_PART.layout" ] || _dl_rm_aux
  if [ "$n" -le 1 ]; then _dl_collapse; _dl_single; return; fi
  read -r rc code total eff <<< "$(_dl_probe "$_DL_URL" "$_DL_PART.hdr")"; rm -f "$_DL_PART.hdr"
  if [ -f "$_DL_PART.layout" ]; then   # ranges from an earlier run
    ltotal=$(head -1 "$_DL_PART.layout")
    if [ "$code" = 206 ] && [ "$total" = "$ltotal" ]; then
      printf '%s\n' "$eff" > "$_DL_PART.url"; _dl_ranges_or_single; return
    elif [ "$code" = 206 ]; then
      echo "  $_DL_NAME: the file on the server changed size ($ltotal -> $total bytes): starting over"; _dl_discard; _DL_OLD=0
    elif [ "$rc" = 63 ] || [ "$code" = 200 ] || [ "$code" = 416 ]; then
      echo "  $_DL_NAME: the server no longer serves ranges (HTTP $code): continuing on one connection"; _dl_collapse; _dl_single; return
    else   # the check itself failed (network): resume the ranges through the original URL
      printf '%s\n' "$_DL_URL" > "$_DL_PART.url"; _dl_ranges_or_single; return
    fi
  fi
  if [ "$code" = 206 ] && [ "$total" != none ] && [ "$total" -ge "$_DL_SEGMIN" ] && _dl_layout "$total" "$n"; then
    printf '%s\n' "$eff" > "$_DL_PART.url"; _dl_ranges_or_single; return
  fi
  _dl_single
}

# _dl_main NAME URL SHA256 DEST: download and verify into DEST. Prints one line: ok | sha <got> | fail | stop.
_dl_main() (
  trap - ERR; set +e
  exec 6>&1 1>&2
  _DL_NAME="$1"; _DL_URL="$2"; _DL_PART="$4.part"; _DL_PIDS=""; _DL_N=1
  _DL_STALL=$(_dl_int "${MU_DL_STALL_S:-}" 30); _DL_IDLE=$(_dl_int "${MU_DL_MAX_IDLE:-}" 6)
  _DL_TRIES=$(_dl_int "${MU_DL_MAX_ATTEMPTS:-}" 60); _DL_BMAX=$(_dl_int "${MU_DL_BACKOFF_MAX:-}" 30)
  _DL_SEGMIN=$(_dl_int "${MU_DL_SEG_MIN:-}" 67108864)
  trap '_dl_stop; echo stop >&6; exit 130' INT TERM HUP
  # _DL_OLD: the partial data predates this call. If the result then fails its SHA-256, start once more from scratch.
  _DL_OLD=0; if [ -s "$_DL_PART" ] || [ -f "$_DL_PART.layout" ]; then _DL_OLD=1; fi
  local redo=0 rc got
  while :; do
    _dl_get; rc=$?
    case "$rc" in 0) ;; 130) echo stop >&6; exit 0 ;; *) echo fail >&6; exit 0 ;; esac
    got="$(sha256 "$_DL_PART")"
    if [ "$got" = "$3" ]; then
      if mv -f "$_DL_PART" "$4"; then _dl_rm_aux; echo ok >&6; else echo fail >&6; fi; exit 0
    fi
    if [ "$_DL_OLD" = 1 ] && [ "$redo" = 0 ]; then
      echo "  $_DL_NAME: the partial download from an earlier attempt does not verify: downloading again from the start"
      _dl_discard; _DL_OLD=0; redo=1; continue
    fi
    _dl_discard; echo "sha $got" >&6; exit 0
  done
)
