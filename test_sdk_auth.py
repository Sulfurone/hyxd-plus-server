"""Regression checks for the Swjoy response contract and local authorization."""
import concurrent.futures
import http.client
import json
import socket
import ssl
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from sdk_auth import AuthorizationServer, CALLBACK_PREFIX, HERE


class AuthorizationContractTests(unittest.TestCase):
    def setUp(self):
        self.server = AuthorizationServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.index = "1234567890abcdef" * 2

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            body = json.dumps(payload) if payload is not None else None
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            return response.status, dict(response.getheaders()), json.loads(raw)
        finally:
            connection.close()

    def poll(self, index=None):
        return self.request("POST", "/pgx/sdk/query_auth_info", {
            "auth_index": index or self.index, "login_channel": "swjoydy"})

    def authorize(self, state="", redirect=None):
        uri = redirect or "https://mgbcb.matrix.netease.com" + CALLBACK_PREFIX + self.index
        return self.request("GET", "/oauth2/authorize.do?" + urlencode({
            "client_id": "3525", "response_type": "code", "redirect_uri": uri,
            "state": state}))

    def test_sdk_success_requires_numeric_200_and_nested_strings(self):
        self.assertEqual(self.poll()[2], {"code": 445})
        state = "a&b=授权 /+"
        status, headers, _ = self.authorize(state)
        self.assertEqual(status, 302)
        callback = urlsplit(headers["Location"])
        arguments = parse_qs(callback.query)
        result = self.poll()[2]
        self.assertEqual(result["code"], 200)
        self.assertIsInstance(result["code"], int)
        self.assertEqual(result["data"]["state"], state)
        self.assertEqual(result["data"]["code"], arguments["code"][0])
        self.assertEqual(arguments["state"], [state])
        self.assertEqual(self.poll()[2], result)
        self.assertEqual(self.request("GET", callback.path + "?" + callback.query)[2],
                         {"authorized": True})

    def test_concurrent_retries_keep_one_session(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.authorize("same"), range(4)))
        self.assertEqual(len({reply[1]["Location"] for reply in replies}), 1)
        self.assertEqual(len(self.server.sessions), 1)
        self.assertEqual(self.poll("abcdef0123456789" * 2)[2], {"code": 445})

    def test_expired_session_returns_pending(self):
        self.authorize()
        with self.server.session_lock:
            self.server.sessions[self.index]["expires"] = time.monotonic() - 1
        self.assertEqual(self.poll()[2], {"code": 445})

    def test_explicit_local_login_issues_stable_session_without_browser(self):
        self.server.local_login = True
        result = self.poll()[2]
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"]["state"], "")
        self.assertEqual(len(result["data"]["code"]), 48)
        self.assertEqual(self.poll()[2], result)

    def test_observed_client_index_length_works_for_authorize_and_poll(self):
        self.index = "a" * 37
        self.assertEqual(self.authorize()[0], 302)
        self.assertEqual(self.poll()[2]["code"], 200)

    def test_malformed_or_unknown_requests_do_not_succeed(self):
        self.assertEqual(self.authorize(redirect="https://example.com/")[0], 400)
        self.assertEqual(self.request("POST", "/pgx/sdk/query_auth_info", ["invalid"])[0], 400)
        self.assertEqual(self.request("POST", "/pgx/sdk/query_auth_info", {
            "auth_index": self.index, "login_channel": "other"})[0], 400)
        self.assertEqual(self.request("GET", "/s/chatft.s")[0], 404)

    def test_capacity_exhaustion_returns_without_deadlock(self):
        with self.server.session_lock:
            self.server.sessions.update({f"{i:032x}": {"expires": time.monotonic() + 30}
                                         for i in range(256)})
        self.assertEqual(self.authorize()[0], 503)

    def test_invalid_tls_client_does_not_block_next_request(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(HERE / "oauth.pem", HERE / "oauth.key")
        self.server.tls_context = context
        with socket.create_connection(self.server.server_address, timeout=3) as plain:
            plain.sendall(b"GET / HTTP/1.0\r\n\r\n")
            try:
                plain.recv(1024)
            except ConnectionResetError:
                pass
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.server.server_port, context=client, timeout=3)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["service"], "pgx-sdk-authorization")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
