#!/bin/sh
set -eu

usage() {
  printf '%s\n' 'Usage: sh qbit-terminal.sh leap|pins|qord main|testnet4 working-directory' >&2
  exit 2
}

[ "$#" -eq 3 ] || usage
protocol=$1
chain=$2
case "$protocol" in leap|pins|qord) ;; *) usage ;; esac
case "$chain" in main|testnet4) ;; *) usage ;; esac
files=$(realpath -- "$3")
[ -d "$files" ] || usage

service() {
  ids=$(docker ps --quiet --filter "label=com.docker.compose.project=$1" \
    --filter "label=com.docker.compose.service=$2")
  case "$ids" in
    ''|*[!0-9a-f]*)
      printf 'Expected one running %s service in %s.\n' "$2" "$1" >&2
      return 1 ;;
  esac
  printf '%s\n' "$ids"
}

core=$(service "qbitleap-core-$chain" core)
indexer=$(service "qbitleap-$protocol-$chain" indexer)
image=$(docker inspect --format '{{.Image}}' "$indexer")
auth=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/auth"}}{{.Source}}{{end}}{{end}}' "$core")
index=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$indexer")
[ -d "$auth" ] && [ -d "$index" ] || {
  printf '%s\n' 'Could not find the Core authentication and index directories.' >&2
  exit 1
}
if [ "$protocol" != pins ] && ! docker exec "$core" /opt/qbit/bin/qbit-cli "-chain=$chain" -datadir=/data \
    -rpcport=18443 -rpccookiefile=/auth/.cookie listwallets >/dev/null; then
  printf '%s\n' 'Start Qbit Core with QBIT_WALLET_ENABLED=1 before opening this terminal.' >&2
  exit 1
fi

set -- --rm -it --init --user 1000:1000 --read-only \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --network "container:$core" --entrypoint /bin/sh --workdir /files \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
  --mount "type=bind,src=$auth,dst=/auth,readonly" \
  --mount "type=bind,src=$files,dst=/files" \
  -e "QBIT_CHAIN=$chain"

case "$protocol" in
  leap)
    set -- "$@" --mount "type=bind,src=$index,dst=/data" \
      -e LEAP_RPC_URL=http://127.0.0.1:18443/ \
      -e LEAP_INDEX_DATA_DIR=/data -e LEAP_NAME_CACHE_DIR=/data/name-cache ;;
  qord)
    qord_chain=$chain
    if [ "$chain" = main ]; then qord_chain=mainnet; fi
    QORD_SERVER_PASSWORD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["server_password"])' \
      "$index/app-qord.yaml")
    export QORD_SERVER_PASSWORD
    set -- "$@" --mount "type=bind,src=$index,dst=/data" \
      -e "QORD_CHAIN=$qord_chain" -e QORD_QBIT_RPC_URL=http://127.0.0.1:18443 \
      -e QORD_COOKIE_FILE=/auth/.cookie -e QORD_CONFIG_DIR=/tmp -e QORD_DATA_DIR=/data \
      -e "QORD_SERVER_URL=http://qbitleap-qord-${chain}_transport_1:8787" \
      -e QORD_SERVER_USERNAME=index -e QORD_SERVER_PASSWORD ;;
esac
exec docker run "$@" "$image" -i
