"""Local protocol-43 BaseApp channel for the 3.0.43 single-player experiment."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time
import uuid

from mercury_protocol import channel_packet, channel_packets, login_request
from mercury_cipher import encrypt, packet_decode, packet_encode

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
LOCK = threading.Lock()
kernel = c.WinDLL("kernel32", use_last_error=True)
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.CloseHandle.argtypes = [w.HANDLE]
kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
kernel.GetProcessTimes.argtypes = [w.HANDLE, c.POINTER(w.FILETIME),
                                  c.POINTER(w.FILETIME), c.POINTER(w.FILETIME),
                                  c.POINTER(w.FILETIME)]


def event(**data):
    with LOCK:
        with (LOGS / "mercury.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), **data}) + "\n")


def local_key():
    try:
        meta = json.loads((LOGS / "client_session.json").read_text())
        handle = kernel.OpenProcess(0x1000, False, meta["pid"])
        if not handle:
            return None
        try:
            path = c.create_unicode_buffer(32768)
            size = w.DWORD(len(path))
            if not kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                return None
            if Path(path.value).resolve() != (HERE.parent / "game/hyp_beta.exe").resolve():
                return None
            created, exited, system, user = (w.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                          c.byref(system), c.byref(user)):
                return None
            created_at = ((created.dwHighDateTime << 32) | created.dwLowDateTime)
            created_at = created_at / 10_000_000 - 11_644_473_600
            if meta["captured"] < created_at:
                return None
        finally:
            kernel.CloseHandle(handle)
        key = (LOGS / "client_session.bin").read_bytes()
        return key if len(key) == meta["key_bytes"] else None
    except (OSError, ValueError, KeyError):
        return None


def login_reply(request, key, port=25100):
    record = (socket.inet_aton("127.0.0.1") + struct.pack(">H", port)
              + b"\x00\x00" + struct.pack("<I", SESSION_KEY) + b"\x00" * 8)
    # This client copies a 20-byte record at RVA 0x41ff25, then rounds the
    # encrypted stream to 8-byte blocks. Stock engine records are shorter.
    encrypted = encrypt(record.ljust(24, b"\x00"), key)
    payload = struct.pack("<I", request.reply_id) + b"\x01" + encrypted
    return struct.pack("<HBI", 0, MESSAGE_REPLY, len(payload)) + payload


SESSION_KEY = 0x12345678

FLAG_HAS_REQUESTS = 0x01
FLAG_HAS_ACKS = 0x04
FLAG_ON_CHANNEL = 0x08
FLAG_IS_RELIABLE = 0x10
FLAG_HAS_SEQUENCE_NUMBER = 0x40
MESSAGE_REPLY = 255
SPACE_DATA_MAPPING_KEY = 1
MAP_PATH = b"universes/eg/hukou"


def fixed_message(method, body):
    return bytes([method]) + body


def variable_message(method, body):
    return bytes([method]) + struct.pack("<H", len(body)) + body


def packet(messages, flags, sequence, acks=(), first_request=None):
    """Build the packet body and footers in PGX 3.0.43 wire order."""
    footer = bytearray()
    if first_request is not None:
        flags |= FLAG_HAS_REQUESTS
        footer += struct.pack("<H", first_request)
    if sequence is not None:
        flags |= FLAG_HAS_SEQUENCE_NUMBER
        footer += struct.pack("<I", sequence)
    if acks:
        flags |= FLAG_HAS_ACKS
        footer += b"".join(struct.pack("<I", ack) for ack in reversed(acks))
        footer += bytes([len(acks)])
    return struct.pack("<H", flags) + b"".join(messages) + bytes(footer)


def base_login_reply(reply_id, key):
    payload = struct.pack("<II", reply_id, SESSION_KEY)
    clear = struct.pack("<HBI", 8, MESSAGE_REPLY, len(payload)) + payload
    return packet_encode(clear, key)


def channel_ack(sequence, key):
    clear = packet([], FLAG_HAS_ACKS | FLAG_ON_CHANNEL, None,
                   acks=(sequence,))
    return packet_encode(clear, key)


def channel_reliable_ack(sequence, key):
    """Reliable ACK form observed from the client after channel setup."""
    clear = packet([], FLAG_HAS_ACKS | FLAG_ON_CHANNEL | FLAG_IS_RELIABLE,
                   None, acks=(sequence,))
    return packet_encode(clear, key)


def geometry_mapping(space_id):
    # The local space has one mapping at the origin. Its entry ID uses the
    # loopback address, a zero port, and salt 1.
    entry_id = socket.inet_aton("127.0.0.1") + struct.pack("<HH", 0, 1)
    header = struct.pack("<I", space_id) + entry_id + struct.pack("<H", SPACE_DATA_MAPPING_KEY)
    transform = struct.pack("<16f", 1, 0, 0, 0,
                                     0, 1, 0, 0,
                                     0, 0, 1, 0,
                                     0, 0, 0, 1)
    return variable_message(7, header + transform + MAP_PATH)


def entity_messages(sequence, key, *, acknowledged_sequence):
    player_id = 1001
    space_id = 1
    position = struct.pack("<fff", 0.0, 100.0, 0.0)
    direction = struct.pack("<fff", 0.0, 0.0, 0.0)
    messages = [
        fixed_message(0, struct.pack("<I", SESSION_KEY)),
        fixed_message(1, struct.pack("<I", 1048576)),
        fixed_message(2, bytes([10])),
        fixed_message(3, struct.pack("<I", 1000)),
        variable_message(5, struct.pack("<IH", player_id, 3)),
        variable_message(6, struct.pack("<II", space_id, 0) + position + direction),
        geometry_mapping(space_id),
        fixed_message(89, struct.pack("<IB", player_id, 1)),
    ]
    return packet_encode(packet(messages, FLAG_ON_CHANNEL | FLAG_IS_RELIABLE,
                                sequence, acks=(acknowledged_sequence,)), key)


def requests_entities(body):
    # Captures start with authenticate (method 1, uint32 session), then
    # enableEntities (method 8, eight bytes) in the client-to-BaseApp direction.
    return len(body) >= 14 and body.startswith(struct.pack("<BIB", 1, SESSION_KEY, 8))


SESSIONS = {}
SESSION_LOCK = threading.Lock()


class LoginHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, endpoint = self.request
        # Ignore echoes of our own reply.  They share UDP port 25000 with the
        # client login socket and would otherwise be parsed as malformed
        # requests during local integration.
        if len(data) >= 3 and struct.unpack_from("<HB", data)[0:2] == (0, MESSAGE_REPLY):
            return
        name = "hall_login_%d_%s.bin" % (time.time_ns(), uuid.uuid4().hex)
        (LOGS / name).write_bytes(data)
        try:
            request = login_request(data)
            if request.version != 43:
                raise ValueError("unsupported login version")
            key = local_key()
            if key is None:
                event(event="waiting_for_local_key", capture=name)
                return
            reply = login_reply(request, key)
            endpoint.sendto(reply, self.client_address)
            event(event="login_reply", version=request.version, size=len(reply), capture=name)
        except (ValueError, OSError) as error:
            event(event="login_error", reason=str(error), capture=name)


class BaseHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, endpoint = self.request
        name = "base_request_%d_%s.bin" % (time.time_ns(), uuid.uuid4().hex)
        (LOGS / name).write_bytes(data)
        peer_port = self.client_address[1]
        event(event="base_datagram", size=len(data), capture=name,
              peer_port=peer_port)
        if len(data) == 21 and struct.unpack_from("<HBH", data) == (1, 0, 8):
            reply_id, next_request, session, attempt = struct.unpack_from("<IHII", data, 5)
            if next_request != 0 or session != SESSION_KEY or data[-2:] != b"\x02\x00":
                event(event="invalid_base_login", capture=name)
                return
            key = local_key()
            if key is None:
                event(event="base_waiting_for_key", capture=name)
                return
            response = base_login_reply(reply_id, key)
            endpoint.sendto(response, self.client_address)
            with SESSION_LOCK:
                SESSIONS[peer_port] = {"sequence": 0, "bootstrap": None,
                                       "bootstrap_acked": False}
            event(event="base_login_reply", attempt=attempt, size=len(response), capture=name)
            return
        key = local_key()
        if key is None:
            return
        try:
            clear = packet_decode(data, key)
            if len(clear) < 3:
                raise ValueError("short BaseApp packet")
            (LOGS / name.replace(".bin", ".decoded.bin")).write_bytes(clear)
            for decoded in channel_packets(clear):
                flags = decoded.flags
                client_sequence = decoded.sequence
                event(event="base_decoded", flags=flags, first_method=decoded.body[0] if decoded.body else None,
                      size=len(clear), client_sequence=client_sequence,
                      client_acks=decoded.acks,
                      piggybacks=len(decoded.piggybacks), capture=name)
                with SESSION_LOCK:
                    session = SESSIONS.setdefault(peer_port, {"sequence": 0,
                        "bootstrap": None, "bootstrap_acked": False})
                    if session["bootstrap"] is not None and (
                            session["bootstrap_sequence"] in decoded.acks or
                            (decoded.cumulative_ack is not None and
                             decoded.cumulative_ack == session["bootstrap_sequence"] + 1)):
                        session["bootstrap_acked"] = True
                    if client_sequence is not None and requests_entities(decoded.body):
                        if session["bootstrap"] is None:
                            sequence = session["sequence"]
                            session["bootstrap"] = entity_messages(
                                sequence, key, acknowledged_sequence=client_sequence)
                            session["bootstrap_sequence"] = sequence
                            session["sequence"] += 1
                            event(event="entity_bootstrap", sequence=sequence,
                                  size=len(session["bootstrap"]), capture=name)
                        if not session["bootstrap_acked"]:
                            endpoint.sendto(session["bootstrap"], self.client_address)
                        else:
                            endpoint.sendto(channel_ack(client_sequence, key),
                                            self.client_address)
                    elif client_sequence is not None and (flags & FLAG_IS_RELIABLE):
                        endpoint.sendto(channel_ack(client_sequence, key),
                                        self.client_address)
                        event(event="channel_ack", sequence=client_sequence, capture=name)
        except ValueError as error:
            event(event="base_decode_error", reason=str(error), capture=name)


class Server(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True
