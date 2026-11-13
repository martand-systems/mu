# μ relay, Stage A (docs/PLAN.md §7): chisel behind the server's Traefik. Built by Dokploy on the server from this
# folder; nothing else is copied in. The base image is pinned by digest and the chisel release by SHA-256.
#
# To update chisel: change CHISEL_VERSION and both sums (from chisel_<version>_checksums.txt on the GitHub release),
# push, and let tools/s4 run in CI before deploying.
ARG ALPINE=alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

FROM ${ALPINE} AS fetch
# v1.12.0 = jpillora/chisel fe4f4fe (2026-08-29), the latest stable release (1.13.0 is a prerelease).
ARG CHISEL_VERSION=1.12.0
ARG CHISEL_SHA256_AMD64=f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6
ARG CHISEL_SHA256_ARM64=2ec6152cd2c74fe0146d4d79e4e7aa174521368c56e433d55e023a92ea404ec3
RUN set -eu; \
    case "$(apk --print-arch)" in \
      x86_64)  arch=amd64; sum="$CHISEL_SHA256_AMD64" ;; \
      aarch64) arch=arm64; sum="$CHISEL_SHA256_ARM64" ;; \
      *) echo "unsupported architecture $(apk --print-arch)" >&2; exit 1 ;; \
    esac; \
    wget -q -O /tmp/chisel.gz \
      "https://github.com/jpillora/chisel/releases/download/v${CHISEL_VERSION}/chisel_${CHISEL_VERSION}_linux_${arch}.gz"; \
    echo "${sum}  /tmp/chisel.gz" | sha256sum -c -; \
    gunzip -c /tmp/chisel.gz > /chisel; chmod 0755 /chisel; \
    [ "$(/chisel --version)" = "${CHISEL_VERSION}" ]

FROM ${ALPINE}
COPY --from=fetch /chisel /usr/local/bin/chisel
COPY entrypoint.sh /usr/local/bin/relay-entrypoint
# No named user is needed: the process only reads its two files and writes /run/relay (a tmpfs, see compose.yaml).
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "/usr/local/bin/relay-entrypoint"]
