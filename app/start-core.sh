#!/bin/sh
set -eu
if [ "${1:-}" = --version ]; then exec /opt/qbit/bin/qbitd --version; fi
[ "${1:-}" = serve ] && [ "$#" = 1 ] || exit 2
case "${QBIT_CHAIN:-}" in main|testnet4) ;; *) exit 2 ;; esac
case "${QBIT_NETWORK_ACTIVE:-0}" in 0|1) ;; *) exit 2 ;; esac
umask 077
[ ! -L /data/.network ] && [ ! -L /auth/.cookie ] || exit 2
if [ -f /data/.network ]; then
  [ "$(cat /data/.network)" = "$QBIT_CHAIN" ] || {
    printf '%s\n' 'This data directory belongs to a different network.' >&2; exit 2;
  }
else
  # Never adopt an unmarked directory that might contain a different node.
  [ -z "$(find /data -mindepth 1 -maxdepth 1 -print -quit)" ] || {
    printf '%s\n' 'Existing unmarked data requires a reviewed migration.' >&2; exit 2;
  }
  printf '%s\n' "$QBIT_CHAIN" > /data/.network
fi
exec /opt/qbit/bin/qbitd "-chain=$QBIT_CHAIN" -datadir=/data \
  -server=1 -disablewallet=1 -txindex=1 -prune=0 \
  -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport=18443 \
  -rpccookiefile=/auth/.cookie -rest=0 \
  -listen=0 -listenonion=0 -discover=0 -natpmp=0 \
  "-networkactive=${QBIT_NETWORK_ACTIVE:-0}" -dbcache=128 -maxmempool=64 \
  -printtoconsole=1
