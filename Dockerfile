# Standalone Qbit runtime component; no website, indexer, data or credentials.
FROM debian:bookworm-20261104-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018 AS unpack
COPY release/qbit-1.0.0-x86_64-linux-gnu.tar.gz /release/qbit.tar.gz
RUN printf '%s\n' 'ae121af03263b55d530e3f3e8719a71362d0950cba82f54a2bc2d6c437a029b5  /release/qbit.tar.gz' | sha256sum --check - \
    && mkdir -p /opt/qbit \
    && tar -xzf /release/qbit.tar.gz -C /opt/qbit --strip-components=1 \
       qbit-1.0.0/bin/qbitd qbit-1.0.0/bin/qbit-cli \
    && printf '%s\n' \
       'd97c50154f87c4486a18204e3eb84fcab3b0c3d2e294942670589be1f48b3917  /opt/qbit/bin/qbitd' \
       'f1e927180ce38cf958dfa0d4f4eabfcdb2a0cc03d39fc0b04d7b49a3cee9bf84  /opt/qbit/bin/qbit-cli' | sha256sum --check - \
    && chmod 0555 /opt/qbit/bin/qbitd /opt/qbit/bin/qbit-cli

FROM debian:bookworm-20261104-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
COPY --from=unpack /opt/qbit /opt/qbit
COPY COPYING.qbit /usr/share/doc/qbit/COPYING
COPY runtime.lock.json /usr/share/doc/qbit/runtime.lock.json
RUN mkdir -p /data && chown 1000:1000 /data
ENV HOME=/data
LABEL org.opencontainers.image.title="Qbit Core runtime component" \
      org.opencontainers.image.version="1.0.0"
USER 1000:1000
WORKDIR /data
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/qbit/bin/qbitd"]
CMD ["--version"]
