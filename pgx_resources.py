"""Read compressed resource records from the installed PGX 3.0.43 files."""

import struct


_LITERALS = bytes(value ^ 0xD7 for value in range(256))
_FINAL_LITERALS = bytes(value ^ 0x14 for value in range(256))
_MAX_RECORD_SIZE = 8 * 1024 * 1024


def decode_record(archive, offset):
    """Return the record payload and the offset just past its compressed data."""
    if not 0 <= offset <= len(archive) - 4:
        raise ValueError("missing resource size")
    size = struct.unpack_from("<I", archive, offset)[0]
    if not 0 < size <= _MAX_RECORD_SIZE:
        raise ValueError("unsupported resource size")

    cursor = offset + 4
    result = bytearray()

    def read_byte():
        nonlocal cursor
        if cursor >= len(archive):
            raise ValueError("truncated resource record")
        value = archive[cursor]
        cursor += 1
        return value

    def extend_length(length, limit):
        extension = 255
        while extension == 255:
            extension = read_byte()
            length += extension
            if length > limit:
                raise ValueError("resource length exceeds record size")
        return length

    while len(result) < size:
        token = read_byte()
        literal_count = token & 15
        if literal_count == 15:
            literal_count = extend_length(literal_count, size - len(result))
        if literal_count > size - len(result) or cursor + literal_count > len(archive):
            raise ValueError("invalid resource literal length")
        final = len(result) + literal_count == size
        literals = archive[cursor:cursor + literal_count]
        result.extend(literals.translate(_FINAL_LITERALS if final else _LITERALS))
        cursor += literal_count
        if final:
            return bytes(result), cursor

        encoded_distance = read_byte() | (read_byte() << 8)
        distance = encoded_distance ^ 0x1129 ^ literal_count
        if not 0 < distance <= len(result):
            raise ValueError("invalid resource match distance")
        match_count = ((token >> 4) ^ 2) + 4
        if match_count == 19:
            match_count = extend_length(match_count, size - len(result))
        if match_count >= size - len(result):
            raise ValueError("resource match exceeds available space")
        for _ in range(match_count):
            result.append(result[-distance])

    raise ValueError("missing terminal resource literals")
