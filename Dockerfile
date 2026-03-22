ARG BASE_IMAGE=docker.m.daocloud.io/library/debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=mirrors.aliyun.com
RUN set -eux; \
    cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
    mirrors="${APT_MIRROR} mirrors.aliyun.com mirrors.tuna.tsinghua.edu.cn mirrors.ustc.edu.cn repo.huaweicloud.com deb.debian.org"; \
    ok=""; \
    for m in $mirrors; do \
      cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; \
      sed -i "s|deb.debian.org|${m}|g; s|security.debian.org|${m}|g" /etc/apt/sources.list.d/debian.sources; \
      if apt-get update; then \
        if apt-get install -y --no-install-recommends \
          bash curl jq cron ca-certificates tzdata procps \
          python3 python3-flask; then \
          ok="1"; \
          break; \
        fi; \
      fi; \
      rm -rf /var/lib/apt/lists/*; \
    done; \
    test -n "$ok"; \
    rm -f /tmp/debian.sources.bak; \
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
