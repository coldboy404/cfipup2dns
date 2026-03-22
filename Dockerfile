ARG BASE_IMAGE=debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=
RUN set -eux; \
    if [ -n "${APT_MIRROR}" ] && [ "${APT_MIRROR}" != "deb.debian.org" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
        sed -i "s|http://deb.debian.org|http://${APT_MIRROR}|g; s|https://deb.debian.org|https://${APT_MIRROR}|g; s|http://security.debian.org|http://${APT_MIRROR}|g; s|https://security.debian.org|https://${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
      elif [ -f /etc/apt/sources.list ]; then \
        cp /etc/apt/sources.list /tmp/sources.list.bak; \
        sed -i "s|http://deb.debian.org|http://${APT_MIRROR}|g; s|https://deb.debian.org|https://${APT_MIRROR}|g; s|http://security.debian.org|http://${APT_MIRROR}|g; s|https://security.debian.org|https://${APT_MIRROR}|g" /etc/apt/sources.list; \
      fi; \
    fi; \
    apt-get update -o Acquire::Retries=8 -o Acquire::ForceIPv4=true -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      -o Acquire::Retries=8 -o Acquire::ForceIPv4=true -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
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
