FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

ARG TAC_PLUS_REPO=https://github.com/facebook/tac_plus.git
ARG TAC_PLUS_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        flex \
        git \
        libpam0g-dev \
        libwrap0-dev \
        m4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN git clone --depth 1 --branch "${TAC_PLUS_REF}" "${TAC_PLUS_REPO}" .

WORKDIR /build/tacacs-F4.0.4.28

RUN ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libpam0g \
        libwrap0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/sbin/tac_plus /usr/local/sbin/tac_plus
COPY --from=builder /usr/local/lib/ /usr/local/lib/
COPY --from=builder /usr/local/man/ /usr/local/man/

RUN ldconfig \
    && mkdir -p /etc/tacacs+ /var/log/tacacs+ \
    && chmod 755 /etc/tacacs+ /var/log/tacacs+

EXPOSE 49/tcp 49/udp

CMD ["/usr/local/sbin/tac_plus", "-C", "/etc/tacacs+/tac_plus.cfg", "-G", "-d", "2", "-l", "/var/log/tacacs+/tacacs.log"]
