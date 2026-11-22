"""PGX gtRpcChannel framing, verified against the running 3.0.43 client."""
import struct

MAX_FRAME = 262144


def varint(value):
    if value < 0 or value >= 1 << 64:
        raise ValueError("varint out of range")
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(data, offset):
    result = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("truncated varint")
        item = data[offset]
        offset += 1
        if shift == 63 and item > 1:
            raise ValueError("varint overflow")
        result |= (item & 127) << shift
        if item < 128:
            return result, offset
    raise ValueError("varint overflow")


def fields(data):
    """Return repeated protobuf fields without decoding account values to logs."""
    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number, kind = tag >> 3, tag & 7
        if not 0 < number < 1 << 29:
            raise ValueError("invalid field number")
        if kind == 0:
            value, offset = read_varint(data, offset)
        elif kind in (1, 2, 5):
            if kind == 2:
                size, offset = read_varint(data, offset)
            else:
                size = 8 if kind == 1 else 4
            if size > len(data) - offset:
                raise ValueError("truncated field")
            value = data[offset:offset + size]
            offset += size
        else:
            raise ValueError("unsupported wire type")
        result.setdefault(number, []).append((kind, value))
    return result


def message(values):
    result = bytearray()
    for number, value in values:
        if isinstance(value, str):
            value = value.encode("utf-8")
        if isinstance(value, bytes):
            result += varint((number << 3) | 2) + varint(len(value)) + value
        else:
            result += varint(number << 3) + varint(value)
    return bytes(result)


def frame(method, payload):
    if not 0 <= method <= 65535 or len(payload) + 2 > MAX_FRAME:
        raise ValueError("invalid frame")
    return struct.pack("<IH", len(payload) + 2, method) + payload


class FrameReader:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 4:
            size = struct.unpack_from("<I", self.buffer)[0]
            if not 2 <= size <= MAX_FRAME:
                raise ValueError("invalid frame size")
            if len(self.buffer) < size + 4:
                break
            method = struct.unpack_from("<H", self.buffer, 4)[0]
            result.append((method, bytes(self.buffer[6:size + 4])))
            del self.buffer[:size + 4]
        return result
