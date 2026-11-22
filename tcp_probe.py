"""Record a loopback protocol without inventing a server response."""
import argparse
import json
import socket
import socketserver
import threading
import time
from pathlib import Path
from mercury_protocol import login_request

LOGS = Path(__file__).resolve().parent / "logs"
LOCK = threading.Lock()


def event(**fields):
    with LOCK:
        with (LOGS / "tcp_probe.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), **fields}) + "\n")


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        port = self.server.server_address[1]
        name = "tcp_%d_%d.bin" % (port, time.time_ns())
        event(port=port, event="connect", capture=name)
        self.request.settimeout(15)
        total = 0
        try:
            with (LOGS / name).open("wb") as stream:
                while total < 1024 * 1024:
                    data = self.request.recv(min(65536, 1024 * 1024 - total))
                    if not data:
                        break
                    stream.write(data)
                    stream.flush()
                    total += len(data)
                    event(port=port, event="receive", size=len(data), total=total)
        except (OSError, socket.timeout) as error:
            event(port=port, event=type(error).__name__)
        finally:
            event(port=port, event="close", total=total)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class DatagramHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, _ = self.request
        port = self.server.server_address[1]
        name = "udp_%d_%d.bin" % (port, time.time_ns())
        (LOGS / name).write_bytes(data)
        event(port=port, event="datagram", capture=name, size=len(data),
              peer_port=self.client_address[1])
        try:
            request = login_request(data)
        except ValueError:
            return
        event(port=port, event="mercury_login", version=request.version,
              payload_size=len(request.payload), capture=name)


class DatagramServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ports", type=int, nargs="+")
    parser.add_argument("--udp", action="store_true", help="Also record UDP datagrams")
    args = parser.parse_args()
    LOGS.mkdir(exist_ok=True)
    servers = []
    try:
        for port in args.ports:
            server = Server(("127.0.0.1", port), Handler)
            servers.append(server)
            if args.udp:
                servers.append(DatagramServer(("127.0.0.1", port), DatagramHandler))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print("Recording 127.0.0.1:%d" % server.server_address[1], flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
