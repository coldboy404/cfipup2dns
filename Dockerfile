ARG BASE_IMAGE=docker.m.daocloud.io/library/debian:bookworm-slim
FROM ${BASE_IMAGE}

RUN set -eux; \
    apt-get -o Acquire::Retries=8 -o Acquire::ForceIPv4=true update; \
    DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=8 -o Acquire::ForceIPv4=true install -y --no-install-recommends \
      bash curl jq cron ca-certificates tzdata procps \
      python3 python3-flask; \
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
