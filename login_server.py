"""Loopback PGX login protocol integration experiment.

The --bootstrap-account option emits the extracted UniSDKReply message after
FetchKey. It does NOT implement RSA challenge recovery or prove lobby support.
"""
import argparse
import json
import socketserver
import threading
import time
from pathlib import Path

from gt_protocol import FrameReader, fields, frame, message

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
LOCK = threading.Lock()


def record(**data):
    with LOCK:
        with (LOGS / "login.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), **data}) + "\n")


def local_account():
    # Required field numbers and types come from login.descriptor.bin.
    return message([(1, "ok"), (2, 1), (3, "local@hyp.invalid"), (4, 0),
                    (5, 201), (6, 10001), (7, 1), (8, "0"),
                    (11, "swjoydy"), (12, 1)])


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        connection = time.time_ns()
        parser = FrameReader()
        total = 0
        record(event="connect", connection=connection)
        self.request.settimeout(300)
        try:
            with (LOGS / ("login_%d.bin" % connection)).open("wb") as capture:
                while total < 1024 * 1024:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    capture.write(data)
                    capture.flush()
                    total += len(data)
                    for method, payload in parser.feed(data):
                        parsed = fields(payload)
                        record(event="request", connection=connection, method=method,
                               size=len(payload), fields=sorted(parsed))
                        if method == 4 and parsed.get(1, [(None, None)])[0][0] == 2:
                            if self.server.bootstrap_account:
                                response = frame(7, local_account())
                                self.request.sendall(response)
                                record(event="experimental_account_reply", connection=connection,
                                       method=7, size=len(response))
                        elif method == 11 and self.server.bootstrap_account:
                            self.request.sendall(frame(7, local_account()))
                            record(event="account_reply", connection=connection, method=7)
                        elif method == 3 and self.server.bootstrap_account:
                            group = message([(1, 201), (2, 10001), (3, 1)])
                            reply = message([(1, bytes.fromhex("00000000000000000000000000000001")),
                                             (2, bytes.fromhex("00000000000000000000000000000002")),
                                             (3, group)])
                            self.request.sendall(frame(1, reply))
                            record(event="query_reply", connection=connection, method=1)
                        elif method == 10 and self.server.bootstrap_account:
                            self.request.sendall(frame(6, message([(1, 201), (2, 10001)])))
                            record(event="group_reply", connection=connection, method=6)
        except (OSError, ValueError) as error:
            record(event="close_reason", connection=connection, reason=type(error).__name__)
        finally:
            record(event="close", connection=connection, received=total)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bootstrap-account", action="store_true")
    args = parser.parse_args()
    LOGS.mkdir(exist_ok=True)
    with Server(("127.0.0.1", args.port), Handler) as server:
        server.bootstrap_account = args.bootstrap_account
        print("PGX login experiment on 127.0.0.1:%d" % args.port, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
