FROM python:3.11-slim

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
    MCIS_TAG=v0.2.3

EXPOSE 9527
VOLUME ["/data"]

ENTRYPOINT ["/opt/cfipup2dns/docker/entrypoint.sh"]
