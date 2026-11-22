import unittest
from mercury_cipher import encrypt, decrypt, packet_encode, packet_decode


class CipherTests(unittest.TestCase):
    def test_blowfish_known_vector_and_plaintext_chain(self):
        key = b"\x00" * 8
        # Published Blowfish vector; XOR of two equal plaintext blocks is zero.
        expected = bytes.fromhex("4ef997456198dd78")
        self.assertEqual(encrypt(b"\x00" * 8, key), expected)
        self.assertEqual(encrypt(b"12345678" * 2, key)[8:], expected)
        self.assertEqual(decrypt(encrypt(b"12345678" * 3, key), key), b"12345678" * 3)

    def test_protocol_43_packet_padding_all_block_boundaries(self):
        for length in range(1, 40):
            original = bytes(range(length))
            encoded = packet_encode(original, b"test")
            self.assertEqual(len(encoded) % 8, 0)
            self.assertEqual(packet_decode(encoded, b"test"), original)
        for data in (b"", b"x", b"1234567"):
            with self.assertRaises(ValueError):
                packet_decode(data, b"test")


if __name__ == "__main__":
    unittest.main()
