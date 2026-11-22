import struct
import unittest

from mercury_protocol import (FLAG_HAS_ACKS, FLAG_HAS_CUMULATIVE_ACK,
                              FLAG_HAS_PIGGYBACKS, FLAG_HAS_REQUESTS,
                              FLAG_HAS_SEQUENCE_NUMBER, FLAG_IS_RELIABLE,
                              FLAG_ON_CHANNEL, channel_packet, channel_packets,
                              login_request)


class MercuryTests(unittest.TestCase):
    def test_observed_protocol_43_envelope(self):
        data = (struct.pack("<HBHIHI", 1, 0, 260, 95450, 0, 43)
                + b"x" * 256 + b"\x02\x00")
        request = login_request(data)
        self.assertEqual(request.reply_id, 95450)
        self.assertEqual(request.version, 43)
        self.assertEqual(request.payload, b"x" * 256)

    def test_reject_truncation_and_unsupported_flags(self):
        data = struct.pack("<HBHIHI", 1, 0, 4, 123, 0, 43) + b"\x02\x00"
        self.assertEqual(login_request(data).payload, b"")
        for packet in (data[:-1], data + b"x", b"\x09" + data[1:], data[:-2] + b"\x00\x00"):
            with self.assertRaises(ValueError):
                login_request(packet)

    def test_length_must_account_for_the_whole_datagram(self):
        for declared_size in (254, 266):
            with self.subTest(declared_size=declared_size):
                data = (struct.pack("<HBHIHI", 1, 0, declared_size, 1, 0, 43)
                        + b"x" * 256 + b"\x02\x00")
                with self.assertRaisesRegex(ValueError, "invalid login message length"):
                    login_request(data)

    def test_channel_footer_layout_and_order(self):
        flags = FLAG_HAS_ACKS | FLAG_HAS_SEQUENCE_NUMBER
        data = (struct.pack("<H", flags) + b"\x01\x00\x00\x00"
                + struct.pack("<I", 6) + struct.pack("<I", 4) + bytes([1]))
        parsed = channel_packet(data)
        self.assertEqual(parsed.flags, flags)
        self.assertEqual(parsed.body, b"\x01\x00\x00\x00")
        self.assertEqual(parsed.acks, (4,))
        self.assertIsNone(parsed.cumulative_ack)
        self.assertEqual(parsed.sequence, 6)

    def test_real_captured_reliable_and_ack_packets(self):
        reliable = bytes.fromhex("5800017856341202000000")
        parsed = channel_packet(reliable)
        self.assertEqual(parsed.flags, FLAG_ON_CHANNEL | FLAG_IS_RELIABLE
                         | FLAG_HAS_SEQUENCE_NUMBER)
        self.assertEqual(parsed.body, b"\x01\x78\x56\x34\x12")
        self.assertEqual(parsed.sequence, 2)

        acknowledged = bytes.fromhex("5c000112345678020000000200000001")
        parsed = channel_packet(acknowledged)
        self.assertEqual(parsed.flags, FLAG_ON_CHANNEL | FLAG_IS_RELIABLE
                         | FLAG_HAS_SEQUENCE_NUMBER | FLAG_HAS_ACKS)
        self.assertEqual(parsed.body, b"\x01\x12\x34\x56\x78")
        self.assertEqual(parsed.sequence, 2)
        self.assertEqual(parsed.acks, (2,))

    def test_piggyback_flag_is_not_an_ack(self):
        flags = FLAG_ON_CHANNEL | FLAG_IS_RELIABLE | FLAG_HAS_SEQUENCE_NUMBER \
            | FLAG_HAS_PIGGYBACKS
        attachment = bytes.fromhex("5800017856341209000000")
        data = (struct.pack("<H", flags) + bytes([1, 2, 3])
                + struct.pack("<I", 11) + attachment
                + struct.pack("<h", ~len(attachment)))
        parsed = channel_packet(data)
        self.assertEqual(parsed.body, b"\x01\x02\x03")
        self.assertEqual(parsed.sequence, 11)
        self.assertEqual(parsed.acks, ())
        self.assertEqual(parsed.piggybacks, (attachment,))
        with self.assertRaisesRegex(ValueError, "piggyback length"):
            channel_packet(data[:-2] + struct.pack("<h", -100))

    def test_first_request_offset_does_not_extend_the_message_body(self):
        flags = FLAG_HAS_REQUESTS | FLAG_HAS_SEQUENCE_NUMBER
        data = (struct.pack("<H", flags) + b"\x00\x05\x00\x00\x00\x02\x00"
                + struct.pack("<I", 9))
        parsed = channel_packet(data)
        self.assertEqual(parsed.sequence, 9)
        self.assertEqual(parsed.body, b"\x00\x05\x00\x00\x00")
        self.assertEqual(parsed.first_request, 2)

    def test_bundled_reliable_packets_are_parsed_separately(self):
        data = bytes.fromhex("5a00017856341203000000"
                             "5a00017856341202000000"
                             "5800017856341201000000f4ff"
                             "e7ff")
        packets = channel_packets(data)
        self.assertEqual([packet.sequence for packet in packets], [1, 2, 3])
        self.assertEqual([packet.flags & FLAG_HAS_PIGGYBACKS for packet in packets],
                         [0, FLAG_HAS_PIGGYBACKS, FLAG_HAS_PIGGYBACKS])
        self.assertEqual([packet.body for packet in packets], [bytes.fromhex("0178563412")] * 3)


if __name__ == "__main__":
    unittest.main()
