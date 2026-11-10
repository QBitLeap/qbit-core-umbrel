import base64
from decimal import Decimal
from http.client import HTTPConnection
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from service.rpc import CookieCore, CoreUnavailable, GatewayClient, GENESIS, ReadRefused, ReadRpcError, status, validate_read, wire_json
from service.server import BoundedServer, handler, parse_requests

spec = importlib.util.spec_from_file_location("app_package", ROOT / "scripts/prepare_app_package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CoreTransport:
    def __init__(self, chain="main", genesis=None):
        self.chain = chain
        self.genesis = genesis or GENESIS["main"]
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        method = self.calls[-1]["method"]
        if method == "getblockchaininfo":
            body = json.dumps({"result": {"chain": self.chain}}).encode()
        elif method == "getblockhash":
            body = json.dumps({"result": self.genesis}).encode()
        else:
            body = b'{"result":{"value":0.123456789123456789,"integer":9007199254740993},"error":null}'
        return Response(body)


class RpcTests(unittest.TestCase):
    def test_read_refusal_happens_before_cookie_or_core_access(self):
        client = CookieCore("main", Path("/missing-cookie"), CoreTransport())
        for method in ("sendrawtransaction", "testmempoolaccept", "walletprocesspsbt",
                       "listwallets", "stop", "setnetworkactive", "getrawtransaction/../wallet"):
            with self.subTest(method=method), self.assertRaises(ReadRefused):
                client.call(method, [])

    def test_parameters_and_whole_batches_are_validated(self):
        for method, params in [("getblockhash", [True]), ("getblockhash", [-1]),
                               ("getblock", ["x", 3]), ("getrawmempool", [True]),
                               ("getrawtransaction", ["a" * 64, 3]),
                               ([], []), ("getblockcount", {})]:
            with self.subTest(method=method), self.assertRaises(ReadRefused):
                validate_read(method, params)
        mixed = [{"id": 1, "method": "getblockcount"}, {"id": 2, "method": "stop"}]
        with self.assertRaises(ReadRefused):
            parse_requests(json.dumps(mixed))
        with self.assertRaises(ValueError):
            parse_requests('{"id":1,"method":"getblockcount","method":"stop"}')
        with self.assertRaises(ValueError):
            parse_requests('{"id":NaN,"method":"getblockcount"}')

    def test_cookie_mode_network_genesis_and_exact_amounts(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / "cookie"
            cookie.write_text("__cookie__:private-test-only")
            cookie.chmod(0o600)
            for transport in (CoreTransport("testnet4"), CoreTransport(genesis="b" * 64)):
                with self.assertRaises(CoreUnavailable):
                    CookieCore("main", cookie, transport).call("getblockcount", [])
                self.assertNotIn("getblockcount", [x["method"] for x in transport.calls])
            value = CookieCore("main", cookie, CoreTransport()).call("getblockcount", [])
            self.assertEqual(value["value"], Decimal("0.123456789123456789"))
            self.assertIn('"value":0.123456789123456789', wire_json(value))
            self.assertIn('"integer":9007199254740993', wire_json(value))
            cookie.chmod(0o644)
            with self.assertRaises(CoreUnavailable):
                CookieCore("main", cookie, CoreTransport()).call("getblockcount", [])

    def test_status_readiness_does_not_claim_transaction_acceptance(self):
        class Client:
            def call(self, method, params):
                return {"getblockchaininfo": {"chain": "main", "blocks": 10, "headers": 10,
                            "bestblockhash": "a" * 64, "initialblockdownload": False},
                        "getindexinfo": {"txindex": {"synced": True, "best_block_height": 10}},
                        "getnetworkinfo": {"connections": 2, "networkactive": True}}[method]
        value = status(Client(), "main")
        self.assertTrue(value["chainAndIndexReady"])
        self.assertFalse(value["transactionRoutingAccepted"])


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls
        class Client:
            def call(self, method, params):
                calls.append((method, params))
                return {"value": Decimal("0.123456789123456789")}
        self.server = BoundedServer(("127.0.0.1", 0), handler("broker", Client(), "main",
                          {"reader": "a" * 64, "ui": "b" * 64}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, body, role="reader", token=None, **headers):
        token = token if token is not None else ("a" * 64 if role == "reader" else "b" * 64)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        headers = {"Content-Type": "application/json", "Authorization": "Basic " +
                   base64.b64encode(f"{role}:{token}".encode()).decode(), **headers}
        connection.request("POST", "/", json.dumps(body), headers)
        response = connection.getresponse()
        code, raw = response.status, response.read()
        connection.close()
        return code, raw

    def test_authentication_origin_and_method_gates_precede_upstream(self):
        query = {"id": 1, "method": "getblockcount"}
        self.assertEqual(self.request(query, token="wrong")[0], 401)
        self.assertEqual(self.request(query, token="é")[0], 401)
        self.assertEqual(self.request(query, Origin="https://untrusted.invalid")[0], 403)
        self.assertEqual(self.request({"id": 1, "method": "stop"})[0], 400)
        self.assertEqual(self.calls, [])

    def test_status_token_is_not_a_general_chain_reader(self):
        self.assertEqual(self.request({"id": 1, "method": "getblockcount"}, role="ui")[0], 400)
        self.assertEqual(self.calls, [])

    def test_authorized_batch_preserves_order_ids_and_exact_numbers(self):
        queries = [{"jsonrpc": "1.0", "id": 7, "method": "getblockcount"},
                   {"jsonrpc": "2.0", "id": "two", "method": "getblockhash", "params": [0]}]
        code, raw = self.request(queries)
        self.assertEqual(code, 200)
        value = json.loads(raw, parse_float=Decimal)
        self.assertEqual([x["id"] for x in value], [7, "two"])
        self.assertEqual(value[0]["result"]["value"], Decimal("0.123456789123456789"))


class StatusRoundTripTests(unittest.TestCase):
    def test_tip_and_missing_transaction_errors_survive_both_http_gateways_without_private_error_details(self):
        class Transport:
            code = -8
            def open(self, request, timeout):
                query = json.loads(request.data)
                if query["method"] == "getblockchaininfo":
                    value = {"result": {"chain": "main"}, "error": None}
                elif query["method"] == "getblockhash" and query["params"] == [0]:
                    value = {"result": GENESIS["main"], "error": None}
                else:
                    message = ("No such mempool or blockchain transaction; " if self.code == -5 else "Height out of range; ")
                    value = {"result": None, "error": {"code": self.code,
                             "message": message + "private-cookie /private/operator/path", "data": "private-detail"}}
                    raise HTTPError(request.full_url, 500, "Internal Server Error", {}, Response(json.dumps(value).encode()))
                return Response(json.dumps(value).encode())
        with tempfile.TemporaryDirectory() as name:
            cookie = Path(name) / "cookie"
            cookie.write_text("__cookie__:test-only")
            cookie.chmod(0o600)
            transport = Transport()
            core = BoundedServer(("127.0.0.1", 0), handler("broker", CookieCore("main", cookie, transport),
                                 "main", {"reader": "a" * 64}))
            proxy_client = GatewayClient("http://127.0.0.1:" + str(core.server_port) + "/", "reader", "a" * 64)
            proxy = BoundedServer(("127.0.0.1", 0), handler("broker", proxy_client, "main", {"reader": "b" * 64}))
            end = GatewayClient("http://127.0.0.1:" + str(proxy.server_port) + "/", "reader", "b" * 64)
            threads = []
            try:
                for server in (core, proxy):
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    threads.append(thread)
                for code, method, params in [(-8, "getblockhash", [1]), (-5, "getrawtransaction", ["a" * 64]),
                                              (-28, "getblockhash", [1])]:
                    transport.code = code
                    with self.assertRaises(ReadRpcError) as raised:
                        end.call(method, params)
                    self.assertEqual(raised.exception.code, code)
                    self.assertNotIn("private", str(raised.exception))
                    if code == -5:
                        self.assertEqual(str(raised.exception), "No such mempool or blockchain transaction")
                transport.code = -12345
                with self.assertRaises(CoreUnavailable) as raised:
                    end.call("getblockhash", [1])
                self.assertNotIsInstance(raised.exception, ReadRpcError)
                self.assertNotIn("private", str(raised.exception))
            finally:
                for server in (proxy, core):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()

    def test_real_http_ui_and_gateway_recover_after_cookie_rotation_and_refuse_wrong_genesis(self):
        class Transport:
            expected = "__cookie__:initial-test-only"
            genesis = GENESIS["main"]
            def open(self, request, timeout):
                authorization = base64.b64decode(request.get_header("Authorization").split()[1]).decode()
                if authorization != self.expected:
                    raise OSError("Fixture authentication refused")
                method = json.loads(request.data)["method"]
                value = {"getblockchaininfo": {"chain": "main", "blocks": 0, "headers": 0,
                             "bestblockhash": GENESIS["main"], "initialblockdownload": True},
                         "getblockhash": self.genesis,
                         "getindexinfo": {"txindex": {"synced": True, "best_block_height": 0}},
                         "getnetworkinfo": {"networkactive": False, "connections": 0}}[method]
                return Response(wire_json({"result": value, "error": None}).encode())
        with tempfile.TemporaryDirectory() as name:
            cookie = Path(name) / "cookie"
            transport = Transport()
            cookie.write_text(transport.expected)
            cookie.chmod(0o600)
            broker = BoundedServer(("127.0.0.1", 0), handler("broker", CookieCore("main", cookie, transport),
                                   "main", {"reader": "a" * 64, "ui": "b" * 64}))
            client = GatewayClient("http://127.0.0.1:" + str(broker.server_port) + "/", "ui", "b" * 64)
            ui = BoundedServer(("127.0.0.1", 0), handler("ui", client, "main"))
            threads = []
            def read():
                connection = HTTPConnection("127.0.0.1", ui.server_port, timeout=3)
                connection.request("GET", "/api/status")
                response = connection.getresponse()
                result = response.status, json.loads(response.read())
                connection.close()
                return result
            try:
                for server in (broker, ui):
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    threads.append(thread)
                code, value = read()
                self.assertEqual(code, 200)
                self.assertEqual(value["blocks"], 0)
                self.assertFalse(value["transactionRoutingAccepted"])
                cookie.unlink()
                self.assertEqual(read()[0], 503)
                transport.expected = "__cookie__:rotated-test-only"
                cookie.write_text(transport.expected)
                cookie.chmod(0o600)
                self.assertEqual(read()[0], 200)
                transport.genesis = "b" * 64
                self.assertEqual(read()[0], 503)
            finally:
                for server in (ui, broker):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()


class PackageTests(unittest.TestCase):
    def test_independent_package_has_only_own_state_and_restricted_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "custom-core"
            package.prepare(destination, "custom-core", "testnet4", 4051,
                            "sha256:" + "a" * 64, "sha256:" + "b" * 64, True)
            manifest = json.loads((destination / "umbrel-app.yml").read_text())
            services = json.loads((destination / "docker-compose.yml").read_text())["services"]
            self.assertEqual(manifest["id"], destination.name)
            self.assertEqual(manifest["storage"], {"dataRoot": "data"})
            self.assertNotIn("submission", manifest)
            self.assertEqual(manifest["icon"], "https://raw.githubusercontent.com/QBitLeap/qbit-core-umbrel/main/icon.svg")
            self.assertEqual(services["app_proxy"]["environment"]["APP_HOST"], "custom-core_ui_1")
            self.assertEqual(services["broker"]["network_mode"], "service:core")
            self.assertNotIn("volumes", services["ui"])
            self.assertEqual(services["broker"]["volumes"], ["${APP_DATA_DIR}/data/auth:/auth:ro"])
            for name in ("core", "broker", "ui"):
                self.assertNotIn("ports", services[name])
                self.assertEqual(services[name]["cap_drop"], ["ALL"])
            exports = (destination / "exports.sh").read_text()
            self.assertNotIn("cookie", exports)
            self.assertIn("derive_entropy", exports)
            self.assertEqual(manifest["website"], "https://github.com/Qbit-Org/qbit")
            with self.assertRaises(ValueError):
                package.prepare(destination, "custom-core", "testnet4", 4051,
                                "sha256:" + "a" * 64, "sha256:" + "b" * 64, True)

    def test_floating_images_are_refused(self):
        with self.assertRaises(ValueError):
            package.prepare(Path("/unused"), "custom-core", "main", 4051, "core:latest", "service:latest")


if __name__ == "__main__":
    unittest.main()
