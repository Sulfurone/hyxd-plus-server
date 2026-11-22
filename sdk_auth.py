"""Local authorization contract extracted from 3.0.43 ntunisdkswjoy.dll.

This implements SDK polling. Game account verification and scene synchronization
still need client integration. Bind to loopback for local experiments.
"""
import argparse
import json
import re
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


HERE = Path(__file__).resolve().parent
CALLBACK_PREFIX = "/pgx/cb/authcallback/swjoydy/pc/defaults/"
INDEX_PATTERN = re.compile(r"[0-9a-fA-F]{1,128}\Z")
MAX_BODY = 16 * 1024


class AuthorizationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, log_path=None, ttl=3600, local_login=False):
        super().__init__(address, AuthorizationHandler)
        self.sessions = {}
        self.session_lock = threading.RLock()
        self.ttl = ttl
        self.log_path = log_path
        self.local_login = local_login
        self.tls_context = None

    def process_request_thread(self, request, client_address):
        if self.tls_context is not None:
            try:
                request.settimeout(10)
                request = self.tls_context.wrap_socket(request, server_side=True)
                self.record("TLS", request.version(), 200)
            except OSError as error:
                self.record("TLS", getattr(error, "reason", type(error).__name__), 0)
                request.close()
                return
        super().process_request_thread(request, client_address)

    def record(self, method, path, status):
        # Indices, authorization codes, and query parameters stay out of logs.
        route = path
        if path.startswith(CALLBACK_PREFIX):
            route = CALLBACK_PREFIX + "[index]"
        line = json.dumps({"time": time.time(), "method": method,
                           "path": route, "status": status}) + "\n"
        if self.log_path:
            with self.session_lock:
                with self.log_path.open("a", encoding="utf-8") as log:
                    log.write(line)


class AuthorizationHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LocalPGXAuth/0.1"
    timeout = 10

    def log_message(self, *args):
        pass

    def reply(self, status, payload, *, location=None):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)
        self.server.record(self.command, urlsplit(self.path).path, status)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/server_list.txt":
            payload = (HERE / "protocol" / "local_serverlist.ini").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(payload)
            self.server.record(self.command, parsed.path, 200)
            return
        if parsed.path == "/health":
            self.reply(200, {"service": "pgx-sdk-authorization",
                             "scene_protocol": "unimplemented"})
            return
        if parsed.path.startswith(CALLBACK_PREFIX):
            # Browser callback is a terminal page. SDK obtains credentials by polling.
            index = parsed.path.removeprefix(CALLBACK_PREFIX)
            with self.server.session_lock:
                session = self.server.sessions.get(index)
                valid = session is not None and session["expires"] > time.monotonic()
            self.reply(200 if valid else 404, {"authorized": valid})
            return
        if parsed.path != "/oauth2/authorize.do":
            self.reply(404, {"error": "unknown_route"})
            return
        params = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(params.get(key, [])) != 1 for key in
               ("client_id", "redirect_uri", "response_type")):
            self.reply(400, {"error": "invalid_authorize_parameters"})
            return
        if params["client_id"][0] != "3525" or params["response_type"][0] != "code":
            self.reply(400, {"error": "unsupported_client"})
            return
        redirect = urlsplit(params["redirect_uri"][0])
        index = redirect.path.removeprefix(CALLBACK_PREFIX)
        if (redirect.scheme != "https" or
                redirect.netloc != "mgbcb.matrix.netease.com" or
                not redirect.path.startswith(CALLBACK_PREFIX) or
                not INDEX_PATTERN.fullmatch(index) or redirect.query or redirect.fragment):
            self.reply(400, {"error": "invalid_redirect_uri"})
            return
        state = params.get("state", [""])[0]
        now = time.monotonic()
        with self.server.session_lock:
            expired = [key for key, value in self.server.sessions.items()
                       if value["expires"] <= now]
            for key in expired:
                del self.server.sessions[key]
            session = self.server.sessions.get(index)
            if session is None:
                if len(self.server.sessions) >= 256:
                    self.reply(503, {"error": "session_capacity_reached"})
                    return
                session = {"code": secrets.token_hex(24), "state": state,
                           "expires": now + self.server.ttl}
                self.server.sessions[index] = session
        location = urlunsplit((redirect.scheme, redirect.netloc, redirect.path,
                               urlencode({"code": session["code"],
                                          "state": session["state"]}), ""))
        self.reply(302, {"authorized": True}, location=location)

    def do_POST(self):
        if urlsplit(self.path).path != "/pgx/sdk/query_auth_info":
            # Keep the first post-authorization protocol messages for analysis.
            # Bodies stay in local binary files rather than readable event logs.
            if self.server.log_path and not self.headers.get("Transfer-Encoding"):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 < length <= MAX_BODY:
                        payload = self.rfile.read(length)
                        capture = self.server.log_path.parent / ("request_%d.bin" % time.time_ns())
                        capture.write_bytes(payload)
                        self.server.record("CAPTURE", capture.name, len(payload))
                except (ValueError, OSError):
                    self.server.record("CAPTURE", "body_unavailable", 0)
            self.reply(404, {"error": "unknown_route"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.reply(400, {"error": "unsupported_transfer_encoding"})
            return
        body = b""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self.reply(413, {"error": "invalid_body_length"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete body")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("expected object")
            index = data.get("auth_index", "")
            if not isinstance(index, str) or not INDEX_PATTERN.fullmatch(index):
                raise ValueError("invalid auth_index")
            if data.get("login_channel") != "swjoydy":
                raise ValueError("invalid login_channel")
        except (ValueError, UnicodeError) as error:
            # Preserve failed local-client requests for protocol research, while
            # keeping tokens and indices out of the human-readable event log.
            if self.server.log_path and body:
                capture = self.server.log_path.parent / ("poll_%d.bin" % time.time_ns())
                capture.write_bytes(body)
                self.server.record("POLL_REJECT", str(error), 400)
            self.reply(400, {"error": "invalid_poll_parameters"})
            return
        with self.server.session_lock:
            session = self.server.sessions.get(index)
            if session and session["expires"] <= time.monotonic():
                del self.server.sessions[index]
                session = None
            if session is None and self.server.local_login:
                now = time.monotonic()
                expired = [key for key, value in self.server.sessions.items()
                           if value["expires"] <= now]
                for key in expired:
                    del self.server.sessions[key]
                if len(self.server.sessions) >= 256:
                    self.reply(503, {"error": "session_capacity_reached"})
                    return
                session = {"code": secrets.token_hex(24), "state": "",
                           "expires": now + self.server.ttl}
                self.server.sessions[index] = session
            result = {"code": 445}
            if session:
                result = {"code": 200, "data": {"code": session["code"],
                                                "state": session["state"]}}
        self.reply(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--plain-http", action="store_true",
                        help="Use HTTP for local contract checks")
    parser.add_argument("--local-login", action="store_true",
                        help="Issue a local test session on the first valid SDK poll")
    args = parser.parse_args()
    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    server = AuthorizationServer(("127.0.0.1", args.port),
                                 log_path=log_dir / "authorization.jsonl",
                                 local_login=args.local_login)
    if not args.plain_http:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        context.load_cert_chain(HERE / "oauth.pem", HERE / "oauth.key")
        server.tls_context = context
    print("SDK authorization listening on 127.0.0.1:%d" % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
