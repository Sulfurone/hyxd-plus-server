"""Blowfish with PGX's previous-plaintext XOR, verified at client RVA 0x8b4010."""
from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
from cryptography.hazmat.primitives.ciphers import Cipher, modes


def encrypt(clear, key):
    if len(clear) % 8:
        raise ValueError("unaligned plaintext")
    cipher = Cipher(Blowfish(key), modes.ECB()).encryptor()
    output = bytearray()
    previous = b"\x00" * 8
    for offset in range(0, len(clear), 8):
        block = clear[offset:offset + 8]
        output.extend(cipher.update(bytes(a ^ b for a, b in zip(block, previous))))
        previous = block
    return bytes(output) + cipher.finalize()


def decrypt(encoded, key):
    if len(encoded) % 8:
        raise ValueError("unaligned ciphertext")
    cipher = Cipher(Blowfish(key), modes.ECB()).decryptor()
    output = bytearray()
    previous = b"\x00" * 8
    for offset in range(0, len(encoded), 8):
        raw = cipher.update(encoded[offset:offset + 8])
        block = bytes(a ^ b for a, b in zip(raw, previous))
        output.extend(block)
        previous = block
    cipher.finalize()
    return bytes(output)


def packet_encode(data, key):
    wastage = (8 - ((len(data) + 1) % 8)) % 8 + 1
    return encrypt(data + b"\x00" * (wastage - 1) + bytes([wastage]), key)


def packet_decode(data, key):
    clear = decrypt(data, key)
    if not clear or not 1 <= clear[-1] <= min(8, len(clear)):
        raise ValueError("invalid packet padding")
    return clear[:-clear[-1]]
