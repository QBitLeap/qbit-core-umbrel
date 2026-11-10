"""Method-limited, network-verified Core reads using a local private cookie."""

import base64
import json
from decimal import Decimal
from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

GENESIS = {
    "main": "0000000000004d60aa5d46013991d0a0e2995d89ee98e53068ae196d763e79f2",
    "testnet4": "000000000000796fe86bbc0bf1b66a07e4b4c0676f74b54cf7e5ce8b3f1a0090",
}
MAX_RESPONSE = 64 * 1024 * 1024
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


class ReadRefused(ValueError):
    pass


class CoreUnavailable(RuntimeError):
    pass


class ReadRpcError(CoreUnavailable):
    """Safe read semantics that must survive each authenticated gateway hop."""
    MESSAGES = {-5: "Requested chain object was not found", -8: "Block height or read parameter is out of range",
                -28: "Core is initializing"}

    def __init__(self, code, transaction_missing=False):
        self.code = code
        message = "No such mempool or blockchain transaction" if code == -5 and transaction_missing else self.MESSAGES[code]
        super().__init__(message)


def raise_read_error(error):
    if isinstance(error, dict) and type(error.get("code")) is int and error["code"] in ReadRpcError.MESSAGES:
        missing = isinstance(error.get("message"), str) and error["message"].startswith(
            "No such mempool or blockchain transaction")
        raise ReadRpcError(error["code"], transaction_missing=missing)
    raise CoreUnavailable("Core read failed")


def integer(value, low=0, high=2**31 - 1):
    return type(value) is int and low <= value <= high


def block_id(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def validate_read(method, params):
    if not isinstance(method, str) or not isinstance(params, list):
        raise ReadRefused("Positional parameters are required")
    valid = False
    if method in {"getblockchaininfo", "getblockcount", "getbestblockhash", "getnetworkinfo", "getmempoolinfo"}:
        valid = not params
    elif method == "getindexinfo":
        valid = not params or params == ["txindex"]
    elif method == "getblockhash":
        valid = len(params) == 1 and integer(params[0])
    elif method == "getblockheader":
        valid = (len(params) in (1, 2) and block_id(params[0])
                 and (len(params) == 1 or type(params[1]) is bool))
    elif method == "getblock":
        valid = (len(params) in (1, 2) and block_id(params[0])
                 and (len(params) == 1 or integer(params[1], 0, 3)))
    elif method == "getblockstats":
        valid = (len(params) in (1, 2) and (integer(params[0]) or block_id(params[0]))
                 and (len(params) == 1 or (isinstance(params[1], list)
                      and len(params[1]) <= 32
                      and all(isinstance(x, str) and re.fullmatch(r"[a-z_]{1,32}", x)
                              for x in params[1]))))
    elif method == "getrawtransaction":
        valid = (1 <= len(params) <= 3 and block_id(params[0])
                 and (len(params) < 2 or type(params[1]) is bool or integer(params[1], 0, 2))
                 and (len(params) < 3 or block_id(params[2])))
    elif method == "gettxout":
        valid = (len(params) in (2, 3) and block_id(params[0]) and integer(params[1])
                 and (len(params) == 2 or type(params[2]) is bool))
    elif method == "getrawmempool":
        valid = not params or params == [False]
    if not valid:
        raise ReadRefused("Read method or parameters are not permitted")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CoreUnavailable("RPC redirect refused")


def wire_json(value):
    """Preserve exact QBT amounts as JSON numbers across the read gateway."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Invalid JSON amount")
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + ":" + wire_json(v) for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(wire_json(v) for v in value) + "]"
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


class CookieCore:
    def __init__(self, chain, cookie=Path("/auth/.cookie"), opener=None):
        if chain not in GENESIS:
            raise ValueError("Unsupported Qbit network")
        self.chain, self.cookie = chain, Path(cookie)
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def _call(self, method, params):
        try:
            info = self.cookie.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise CoreUnavailable("Core cookie is unavailable")
            credentials = self.cookie.read_bytes()
            if not 1 <= len(credentials) <= 512 or b":" not in credentials:
                raise CoreUnavailable("Core cookie is unavailable")
            credentials = credentials.strip()
            request = Request("http://127.0.0.1:18443/", method="POST",
                              data=json.dumps({"jsonrpc": "2.0", "id": "read",
                                               "method": method, "params": params}).encode(),
                              headers={"Content-Type": "application/json",
                                       "Authorization": "Basic " + base64.b64encode(credentials).decode()})
            try:
                response = self.opener.open(request, timeout=30)
            except HTTPError as error:
                # Core's legacy JSON-RPC errors may arrive with HTTP500. Parse
                # their bounded JSON before classifying transport availability.
                response = error
            with response:
                body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise CoreUnavailable("Core response exceeds the read limit")
            value = json.loads(body, parse_float=Decimal)
            if not isinstance(value, dict):
                raise CoreUnavailable("Core read failed")
            if value.get("error") is not None:
                raise_read_error(value["error"])
            if getattr(response, "status", 200) != 200:
                raise CoreUnavailable("Core read failed")
            return value["result"]
        except CoreUnavailable:
            raise
        except Exception:
            # Never put private paths, cookie contents or upstream error bodies on the wire.
            raise CoreUnavailable("Core is unavailable") from None

    def call(self, method, params):
        validate_read(method, params)
        info = self._call("getblockchaininfo", [])
        if not isinstance(info, dict) or info.get("chain") != self.chain:
            raise CoreUnavailable("Core network verification failed")
        if self._call("getblockhash", [0]) != GENESIS[self.chain]:
            raise CoreUnavailable("Core genesis verification failed")
        return info if method == "getblockchaininfo" else self._call(method, params)


class GatewayClient:
    """The status UI has read credentials only, never a Core cookie mount."""
    def __init__(self, endpoint, username, token, opener=None, max_response=1024 * 1024):
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Expected an internal read RPC endpoint")
        self.endpoint, self.username, self.token = endpoint, username, token
        if type(max_response) is not int or not 1 <= max_response <= MAX_RESPONSE:
            raise ValueError("Invalid read budget")
        self.max_response = max_response
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method, params):
        validate_read(method, params)
        request = Request(self.endpoint, method="POST", data=json.dumps({
            "jsonrpc": "2.0", "id": "status", "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Basic " +
                     base64.b64encode(f"{self.username}:{self.token}".encode()).decode()})
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read(self.max_response + 1)
            if len(raw) > self.max_response:
                raise CoreUnavailable("Read response too large")
            result = json.loads(raw, parse_float=Decimal)
            if result.get("error") is not None:
                raise_read_error(result["error"])
            return result["result"]
        except CoreUnavailable:
            raise
        except Exception:
            raise CoreUnavailable("Read service is unavailable") from None


def status(client, chain):
    info = client.call("getblockchaininfo", [])
    indexes = client.call("getindexinfo", ["txindex"])
    peers = client.call("getnetworkinfo", [])
    if not all(isinstance(x, dict) for x in (info, indexes, peers)) or info.get("chain") != chain:
        raise CoreUnavailable("Core network verification failed")
    blocks, headers = info.get("blocks"), info.get("headers")
    txindex = indexes.get("txindex", {})
    if not isinstance(txindex, dict):
        raise CoreUnavailable("Core index status is unavailable")
    ready = (integer(blocks) and type(headers) is int and headers == blocks
             and info.get("initialblockdownload") is False and block_id(info.get("bestblockhash"))
             and txindex.get("synced") is True
             and txindex.get("best_block_height") == blocks)
    return {"network": chain, "blocks": blocks, "headers": headers,
            "bestBlockHash": info.get("bestblockhash"),
            "verificationProgress": info.get("verificationprogress"),
            "initialBlockDownload": info.get("initialblockdownload"),
            "txindexSynced": txindex.get("synced", False),
            "txindexHeight": txindex.get("best_block_height"),
            "peerNetworkingEnabled": peers.get("networkactive", False),
            "connections": peers.get("connections", 0), "chainAndIndexReady": ready,
            "transactionRoutingAccepted": False}
