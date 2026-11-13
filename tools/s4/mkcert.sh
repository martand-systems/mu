#!/usr/bin/env bash
# A throw-away CA and server certificates for the S4 replica (the real server gets Let's Encrypt from Traefik).
#
#   mkcert.sh OUTDIR ca                      -> OUTDIR/ca.crt, OUTDIR/ca.key
#   mkcert.sh OUTDIR leaf NAME HOST [HOST…]  -> OUTDIR/NAME.crt, OUTDIR/NAME.key   (signed by OUTDIR/ca.*)
set -euo pipefail
out="$1"; what="$2"; shift 2
mkdir -p "$out"
case "$what" in
  ca)
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3 \
      -subj "/CN=mu S4 test CA" -keyout "$out/ca.key" -out "$out/ca.crt" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null ;;
  leaf)
    name="$1"; shift
    san=""; for h in "$@"; do san="${san:+$san,}DNS:$h"; done
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
      -subj "/CN=$1" -keyout "$out/$name.key" -out "$out/$name.csr" 2>/dev/null
    printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' \
      "$san" > "$out/$name.ext"
    openssl x509 -req -in "$out/$name.csr" -CA "$out/ca.crt" -CAkey "$out/ca.key" -CAcreateserial \
      -days 3 -extfile "$out/$name.ext" -out "$out/$name.crt" 2>/dev/null
    rm -f "$out/$name.csr" "$out/$name.ext" "$out/ca.srl"
    chmod 644 "$out/$name.key" ;;   # test key, read by containers running as other users
  *) echo "usage: mkcert.sh OUTDIR ca | leaf NAME HOST..." >&2; exit 2 ;;
esac
