# Test-only image for the S4 replica: the "compute" and "workstation" ends, the idle-killing hop and the probes.
# chisel is copied out of the relay image built from relay/, so both ends run the same verified binary.
ARG RELAY_IMAGE
FROM ${RELAY_IMAGE} AS relay

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache curl python3
COPY --from=relay /usr/local/bin/chisel /usr/local/bin/chisel
COPY entry.sh service.py alpn_probe.py ws_probe.py idle_killer.py wait_http.py /s4/
RUN mkdir -p /data /certs
ENTRYPOINT ["/bin/sh", "/s4/entry.sh"]
