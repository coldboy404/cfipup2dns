FROM golang:1.24-bookworm AS go-toolchain

FROM python:3.11-slim

COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:${PATH}"

WORKDIR /opt/cfipup2dns

COPY config.example.json ./
COPY cfip.sh ./
COPY cfip_runner.py ./
COPY docker ./docker
COPY web ./web

RUN chmod +x /opt/cfipup2dns/cfip.sh /opt/cfipup2dns/docker/entrypoint.sh /opt/cfipup2dns/docker/up.sh

ENV TZ=Asia/Shanghai \
    PORT=9527 \
    PROJECT_DIR=/data/project \
    CONFIG_FILE=/data/project/config.json \
    CRON_FILE=/data/cron/cfip.cron \
    LOG_FILE=/data/logs/cron.log \
    GH_PROXY=https://gh-proxy.org/ \
    MCIS_REF=main

EXPOSE 9527
VOLUME ["/data"]

ENTRYPOINT ["/opt/cfipup2dns/docker/entrypoint.sh"]
