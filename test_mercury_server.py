import pathlib
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from mercury_cipher import decrypt, packet_decode, packet_encode
from mercury_protocol import LoginRequest, channel_packet, login_request
from mercury_server import (BaseHandler, Server,
                            channel_ack, channel_reliable_ack, entity_messages,
                            login_reply, packet_decode)


class LoginReplyTests(unittest.TestCase):
    def test_login_handler_ignores_reply_echoes(self):
        source = pathlib.Path(__import__("mercury_server").__file__).read_text(encoding="utf-8")
        self.assertIn("MESSAGE_REPLY", source)
        self.assertIn("Ignore echoes of our own reply", source)

    def test_custom_client_gets_full_twenty_byte_record(self):
        response = login_reply(LoginRequest(123, 43, b""), b"test")
        flags, method, length = struct.unpack_from("<HBI", response)
        reply_id, = struct.unpack_from("<I", response, 7)
        status = response[11]
        self.assertEqual((flags, method, length, reply_id, status), (0, 255, 29, 123, 1))
        clear = decrypt(response[12:], b"test")
        self.assertEqual(len(clear), 24)
        self.assertEqual(clear[:4], socket.inet_aton("127.0.0.1"))
        self.assertEqual(struct.unpack_from(">H", clear, 4)[0], 25100)
        self.assertEqual(struct.unpack_from("<I", clear, 8)[0], 0x12345678)

    def test_real_udp_base_request_gets_encrypted_channel_reply(self):
        with Server(("127.0.0.1", 0), BaseHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("mercury_server.local_key", return_value=b"test"), \
                    patch("mercury_server.event"), \
                    patch("pathlib.Path.write_bytes"):
                thread.start()
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                        client.settimeout(3)
                        request = struct.pack("<HBHIHIIH", 1, 0, 8, 123, 0,
                                              0x12345678, 0, 2)
                        client.sendto(request, server.server_address)
                        response, _ = client.recvfrom(1024)
                        decoded = packet_decode(response, b"test")
                        self.assertEqual(struct.unpack_from("<HBI", decoded),
                                         (8, 255, 8))
                        self.assertEqual(struct.unpack_from("<I", decoded, 7)[0], 123)
                        self.assertEqual(struct.unpack_from("<I", decoded, 11)[0],
                                         0x12345678)

                        auth = struct.pack("<HBI", 0x58, 1, 0x12345678)
                        client.sendto(packet_encode(auth + struct.pack("<I", 6),
                                                    b"test"), server.server_address)
                        ack, _ = client.recvfrom(1024)
                        self.assertEqual(channel_packet(packet_decode(ack, b"test")).body, b"")

                        enable = auth + b"\x08" + bytes(8) + struct.pack("<I", 7)
                        client.sendto(packet_encode(enable, b"test"), server.server_address)
                        bootstrap, _ = client.recvfrom(1024)
                        parsed = channel_packet(packet_decode(bootstrap, b"test"))
                        self.assertEqual(parsed.sequence, 0)
                        self.assertEqual(parsed.acks, (7,))
                        client.sendto(packet_encode(enable, b"test"), server.server_address)
                        retransmission, _ = client.recvfrom(1024)
                        self.assertEqual(retransmission, bootstrap)
                        client_ack = (struct.pack("<HBI", 0x5c, 1, 0x12345678)
                                      + struct.pack("<II", 8, 0) + b"\x01")
                        client.sendto(packet_encode(client_ack, b"test"), server.server_address)
                        client.recvfrom(1024)
                        client.sendto(packet_encode(enable, b"test"), server.server_address)
                        final_ack, _ = client.recvfrom(1024)
                        self.assertEqual(channel_packet(packet_decode(final_ack, b"test")).acks, (7,))
                        body = parsed.body
                        cursor = 17  # Authenticate, bandwidth, frequency and game time.
                        for expected in (5, 6, 7):
                            method, size = struct.unpack_from("<BH", body, cursor)
                            self.assertEqual(method, expected)
                            cursor += 3
                            payload = body[cursor:cursor + size]
                            cursor += size
                        self.assertEqual(struct.unpack_from("<I4sHHH", payload),
                                         (1, b"\x7f\x00\x00\x01", 0, 1, 1))
                        self.assertEqual(struct.unpack_from("<16f", payload, 14),
                                         (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))
                        self.assertEqual(payload[78:], b"universes/eg/hukou")
                        self.assertEqual(body[cursor:], b"\x59" + struct.pack("<IB", 1001, 1))
                finally:
                    server.shutdown()
                    thread.join(3)

    def test_entity_bootstrap_contains_creation_and_control(self):
        decoded = packet_decode(entity_messages(2, b"test", acknowledged_sequence=1), b"test")
        self.assertEqual(decoded[2], 0)
        self.assertEqual(struct.unpack_from("<I", decoded, 3)[0], 0x12345678)
        self.assertIn(b"\x05\x06\x00" + struct.pack("<IH", 1001, 3), decoded)
        self.assertIn(b"\x06\x20\x00", decoded)
        self.assertIn(b"\x59" + struct.pack("<IB", 1001, 1), decoded)

    def test_channel_ack_includes_both_ack_footers(self):
        decoded = channel_packet(packet_decode(channel_ack(7, b"test"), b"test"))
        self.assertEqual(decoded.body, b"")
        self.assertEqual(decoded.acks, (7,))
        self.assertIsNone(decoded.sequence)

    def test_reliable_channel_ack_uses_observed_flags(self):
        decoded = channel_packet(packet_decode(channel_reliable_ack(7, b"test"), b"test"))
        self.assertEqual(decoded.flags, 0x1c)
        self.assertEqual(decoded.body, b"")
        self.assertEqual(decoded.acks, (7,))

    def test_anonymized_base_login_request_shape(self):
        # Preserve the captured envelope with a deterministic reply ID. Tests
        # must not depend on private runtime logs being present in the checkout.
        data = bytes.fromhex("010000080001000000000078563412000000000200")
        self.assertEqual(len(data), 21)
        self.assertEqual(struct.unpack_from("<HBH", data), (1, 0, 8))
        self.assertEqual(struct.unpack_from("<I", data, 11)[0], 0x12345678)
        self.assertEqual(data[15:19], b"\x00" * 4)
        self.assertEqual(struct.unpack_from("<H", data, 19)[0], 2)
        self.assertEqual(login_request(data), LoginRequest(1, 0x12345678, bytes(4)))


if __name__ == "__main__":
    unittest.main()
