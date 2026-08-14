FROM golang:1.24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 tar \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    GOENV=off \
    GOTOOLCHAIN=local

WORKDIR /work
