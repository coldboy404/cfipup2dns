ARG BASE_IMAGE=docker.1panel.live/library/debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=deb.debian.org
RUN set -eux; \
    if [ -n "${APT_MIRROR}" ] && [ "${APT_MIRROR}" != "deb.debian.org" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources || true; \
      elif [ -f /etc/apt/sources.list ]; then \
        cp /etc/apt/sources.list /tmp/sources.list.bak; \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list || true; \
      fi; \
    fi; \
    if ! apt-get -o Acquire::Retries=8 -o Acquire::ForceIPv4=true update; then \
      if [ -f /tmp/debian.sources.bak ]; then cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; fi; \
      if [ -f /tmp/sources.list.bak ]; then cp /tmp/sources.list.bak /etc/apt/sources.list; fi; \
      apt-get -o Acquire::Retries=8 -o Acquire::ForceIPv4=true update; \
    fi; \
    DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=8 -o Acquire::ForceIPv4=true install -y --no-install-recommends \
      bash curl jq cron ca-certificates tzdata procps \
      python3 python3-flask; \
    rm -f /tmp/debian.sources.bak /tmp/sources.list.bak; \
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
