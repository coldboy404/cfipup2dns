# syntax=docker/dockerfile:1.7
ARG GO_VERSION=1.25.5

FROM --platform=${BUILDPLATFORM:-linux/amd64} golang:${GO_VERSION}-bookworm AS mcis-builder
ARG TARGETOS
ARG TARGETARCH
ARG MCIS_REF=main
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    target_os="${TARGETOS:-linux}"; \
    target_arch="${TARGETARCH:-amd64}"; \
    case "$MCIS_REF" in \
      main) archive_url="https://github.com/Leo-Mu/montecarlo-ip-searcher/archive/refs/heads/main.tar.gz" ;; \
      *) archive_url="https://github.com/Leo-Mu/montecarlo-ip-searcher/archive/refs/tags/${MCIS_REF}.tar.gz" ;; \
    esac; \
    curl -fL --retry 4 --retry-delay 2 "$archive_url" -o /tmp/mcis.tar.gz; \
    tar -xzf /tmp/mcis.tar.gz --strip-components=1 -C /src; \
    CGO_ENABLED=0 GOOS="$target_os" GOARCH="$target_arch" \
      go build -trimpath -ldflags='-s -w' -o /out/montecarlo-ip-searcher ./cmd/mcis; \
    printf '%s+source' "$MCIS_REF" > /out/.mcis_version; \
    cp ipv4cidr.txt ipv6cidr.txt /out/

FROM python:3.11-slim-bookworm
ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="cfipup2dns" \
      org.opencontainers.image.description="Cloudflare 优选 IP 自动 DDNS 与 WebUI" \
      org.opencontainers.image.source="https://github.com/coldboy404/cfipup2dns" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.licenses="MIT"

ENV TZ=Asia/Shanghai \
    PORT=9527 \
    PROJECT_DIR=/data/project \
    CONFIG_FILE=/data/project/config.json \
    CRON_FILE=/data/cron/cfip.cron \
    LOG_FILE=/data/logs/cron.log \
    MCIS_REF=main \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/cfipup2dns
COPY config.example.json cfip.sh cfip_runner.py ./
COPY docker ./docker
COPY web ./web
COPY --from=mcis-builder /out/ ./bundled-mcis/

RUN chmod +x cfip.sh docker/entrypoint.sh docker/up.sh docker/init-mcis.sh \
    && mkdir -p /data

EXPOSE 9527
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9527/api/health', timeout=3)" || exit 1
ENTRYPOINT ["/opt/cfipup2dns/docker/entrypoint.sh"]
