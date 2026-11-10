#!/bin/sh
set -eu
if [ "${1:-}" = --version ]; then exec /opt/qbit/bin/qbitd --version; fi
[ "${1:-}" = serve ] && [ "$#" = 1 ] || exit 2
case "${QBIT_CHAIN:-}" in main|testnet4) ;; *) exit 2 ;; esac
case "${QBIT_NETWORK_ACTIVE:-1}" in 0|1) ;; *) exit 2 ;; esac
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
network_active="${QBIT_NETWORK_ACTIVE:-1}"
[ ! -L /settings/network-active ] || exit 2
if [ -e /settings/network-active ]; then
  [ -f /settings/network-active ] && [ "$(wc -c < /settings/network-active)" -le 64 ] || exit 2
  IFS=: read -r saved_chain saved_default saved_active saved_extra < /settings/network-active
  [ "$saved_chain" = "$QBIT_CHAIN" ] && [ -z "$saved_extra" ] || exit 2
  case "$saved_default:$saved_active" in 0:0|0:1|1:0|1:1) ;; *) exit 2 ;; esac
  if [ "$saved_default" = "$network_active" ]; then network_active="$saved_active"; fi
fi
network_tmp="/settings/.network-active-start-$$"
( set -C; printf '%s:%s:%s\n' "$QBIT_CHAIN" "${QBIT_NETWORK_ACTIVE:-1}" "$network_active" > "$network_tmp" )
mv -f "$network_tmp" /settings/network-active
chmod 600 /settings/network-active
exec /opt/qbit/bin/qbitd "-chain=$QBIT_CHAIN" -datadir=/data \
  -server=1 -disablewallet=1 -txindex=1 -prune=0 \
  -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport=18443 \
  -rpccookiefile=/auth/.cookie -rest=0 \
  -listen=0 -listenonion=0 -discover=0 -natpmp=0 \
  "-networkactive=$network_active" -dbcache=128 -maxmempool=64 \
  -printtoconsole=1
