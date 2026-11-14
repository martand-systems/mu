# Test-only image for the S4 replica: the "compute" and "workstation" ends, the idle-killing hop and the probes.
# chisel is copied out of the relay image built from relay/, so both ends run the same verified binary.
ARG RELAY_IMAGE
FROM ${RELAY_IMAGE} AS relay

# rawchan: a client that is not chisel's (rawchan/main.go). Its two dependencies are the versions chisel 1.12.0
# itself uses, checked against rawchan/go.sum.
FROM golang:1.26.4-alpine3.24@sha256:3ad57304ad93bbec8548a0437ad9e06a455660655d9af011d58b993f6f615648 AS rawchan
ENV GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0
WORKDIR /src
COPY rawchan/go.mod rawchan/go.sum ./
RUN go mod download
COPY rawchan/main.go ./
RUN go build -trimpath -o /rawchan .

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache curl python3
COPY --from=relay /usr/local/bin/chisel /usr/local/bin/chisel
COPY --from=rawchan /rawchan /usr/local/bin/rawchan
COPY entry.sh service.py alpn_probe.py ws_probe.py ws_flood.py idle_killer.py wait_http.py pair_ref.py /s4/
RUN mkdir -p /data /certs
ENTRYPOINT ["/bin/sh", "/s4/entry.sh"]
