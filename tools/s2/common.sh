# Shared by the S2 probes. Runs as root inside a fresh ubuntu:24.04 (or WSL rootfs) container; /w = repo, /out = results.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
OUT=/out; mkdir -p "$OUT"; T0=$SECONDS
log() { printf '[%4ss] %s\n' "$((SECONDS - T0))" "$*" | tee -a "$OUT/timeline.txt"; }
res() { printf '%s=%s\n' "$1" "$2" | tee -a "$OUT/results.txt"; }   # key=value results
apt_install() { log "apt: $*"; apt-get update -qq; apt-get install -y -qq --no-install-recommends "$@" >/dev/null; }
fetch() {  # fetch URL FILE SHA256
  log "download $(basename "$2")"; curl -fL --retry 3 -sS -o "$2" "$1"
  echo "$3  $2" | sha256sum -c - >/dev/null || { echo "SHA-256 mismatch for $2: $(sha256sum "$2")"; exit 1; }
  res "bytes_$(basename "$2")" "$(stat -c %s "$2")"
}
as_user() { runuser -u mu -- env HOME=/home/mu "$@"; }   # WSL distros run tools as a normal user
id mu >/dev/null 2>&1 || useradd -m -s /bin/bash mu
log "container: $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m), $(nproc) cpus, $(free -g | awk '/Mem/{print $2}') GB"
