import struct
import unittest

from gt_protocol import FrameReader, fields, frame, message


class WireTests(unittest.TestCase):
    def test_observed_fetch_key_layout_and_fragmentation(self):
        packet = bytes.fromhex("b700000004000ab201") + b"a" * 178
        reader = FrameReader()
        replies = []
        for byte in packet:
            replies += reader.feed(bytes([byte]))
        self.assertEqual(replies, [(4, b"\x0a\xb2\x01" + b"a" * 178)])
        self.assertEqual(fields(replies[0][1]), {1: [(2, b"a" * 178)]})

    def test_coalesced_frames_and_repeated_fields(self):
        encoded = message([(1, "local"), (2, 201), (1, b"second")])
        reader = FrameReader()
        self.assertEqual(reader.feed(frame(3, encoded) + frame(10, b"")),
                         [(3, encoded), (10, b"")])
        self.assertEqual(fields(encoded)[1], [(2, b"local"), (2, b"second")])

    def test_invalid_lengths_and_varints(self):
        for size in (0, 1, 262145, 0xffffffff):
            with self.assertRaises(ValueError):
                FrameReader().feed(struct.pack("<I", size))
        for payload in (b"\x0a\x04xx", b"\x08\x80", b"\x00", b"\x08" + b"\xff" * 10):
            with self.assertRaises(ValueError):
                fields(payload)


if __name__ == "__main__":
    unittest.main()
