ARG BASE_IMAGE=docker.m.daocloud.io/library/debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=mirrors.aliyun.com
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://deb.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources || true; \
    elif [ -f /etc/apt/sources.list ]; then \
      sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list || true; \
    fi; \
    ok=""; \
    for i in 1 2 3; do \
      apt-get update && ok=1 && break || sleep 3; \
    done; \
    test -n "$ok"; \
    ok=""; \
    for i in 1 2 3; do \
      apt-get install -y --no-install-recommends \
        bash curl jq cron ca-certificates tzdata procps \
        python3 python3-flask && ok=1 && break || sleep 3; \
    done; \
    test -n "$ok"; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cfipup2dns

COPY cfip.sh config.example.json ./
COPY docker ./docker
COPY web ./web

RUN chmod +x /opt/cfipup2dns/docker/*.sh /opt/cfipup2dns/cfip.sh

ENV TZ=Asia/Shanghai \
    PORT=9527 \
    PROJECT_DIR=/data/project \
    CONFIG_FILE=/data/project/config.json \
    GH_PROXY=https://gh-proxy.org/

EXPOSE 9527
VOLUME ["/data"]

ENTRYPOINT ["/opt/cfipup2dns/docker/entrypoint.sh"]
