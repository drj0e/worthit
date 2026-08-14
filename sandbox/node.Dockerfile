FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 tar \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false

WORKDIR /work
