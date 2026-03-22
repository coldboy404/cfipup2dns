ARG BASE_IMAGE=docker.m.daocloud.io/library/debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=mirrors.aliyun.com
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
      sed -i "s|http://deb.debian.org/debian|http://${APT_MIRROR}/debian|g; s|http://deb.debian.org/debian-security|http://${APT_MIRROR}/debian-security|g; s|http://security.debian.org/debian-security|http://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources || true; \
    elif [ -f /etc/apt/sources.list ]; then \
      cp /etc/apt/sources.list /tmp/sources.list.bak; \
      sed -i "s|http://deb.debian.org/debian|http://${APT_MIRROR}/debian|g; s|http://security.debian.org/debian-security|http://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list || true; \
    fi; \
    if ! apt-get -o Acquire::Retries=5 update; then \
      if [ -f /tmp/debian.sources.bak ]; then cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; fi; \
      if [ -f /tmp/sources.list.bak ]; then cp /tmp/sources.list.bak /etc/apt/sources.list; fi; \
      apt-get -o Acquire::Retries=5 update; \
    fi; \
    apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
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
