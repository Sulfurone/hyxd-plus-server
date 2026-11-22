"""Decode the UDP login envelope and channel packets observed in PGX 3.0.43."""
from dataclasses import dataclass
import struct


FLAG_HAS_REQUESTS = 0x0001
FLAG_HAS_PIGGYBACKS = 0x0002
FLAG_HAS_ACKS = 0x0004
FLAG_ON_CHANNEL = 0x0008
FLAG_IS_RELIABLE = 0x0010
FLAG_IS_FRAGMENT = 0x0020
FLAG_HAS_SEQUENCE_NUMBER = 0x0040
FLAG_INDEXED_CHANNEL = 0x0080
FLAG_HAS_CHECKSUM = 0x0100
FLAG_CREATE_CHANNEL = 0x0200
FLAG_HAS_CUMULATIVE_ACK = 0x0400


@dataclass(frozen=True)
class LoginRequest:
    reply_id: int
    version: int
    payload: bytes


def login_request(data):
    if len(data) < 17:
        raise ValueError("truncated login envelope")
    flags, method, size, reply_id, next_request = struct.unpack_from("<HBHIH", data)
    if flags != 1 or method != 0 or next_request != 0:
        raise ValueError("unsupported login envelope")
    # The length excludes the 11-byte envelope and 2-byte request offset.
    if size < 4 or size + 13 != len(data):
        raise ValueError("invalid login message length")
    if struct.unpack_from("<H", data, len(data) - 2)[0] != 2:
        raise ValueError("invalid first request offset")
    version = struct.unpack_from("<I", data, 11)[0]
    return LoginRequest(reply_id, version, data[15:-2])


@dataclass(frozen=True)
class ChannelPacket:
    flags: int
    body: bytes
    acks: tuple
    cumulative_ack: int | None
    sequence: int | None
    piggybacks: tuple
    first_request: int | None


def channel_packet(data):
    """Separate the message body from transport footers and attached packets."""
    if len(data) < 2:
        raise ValueError("short channel packet")
    flags = struct.unpack_from("<H", data)[0]

    end = len(data)

    piggybacks = []
    if flags & FLAG_HAS_PIGGYBACKS:
        while True:
            end -= 2
            if end < 2:
                raise ValueError("short piggyback length")
            length = struct.unpack_from("<h", data, end)[0]
            last = length < 0
            # The last attachment encountered from the end has an inverted length.
            size = ~length if last else length
            start = end - size
            if size < 2 or start < 2:
                raise ValueError("invalid piggyback length")
            piggybacks.append(data[start:end])
            end = start
            if last:
                break

    if flags & FLAG_HAS_CUMULATIVE_ACK:
        end -= 4
        if end < 2:
            raise ValueError("short cumulative ack")
        cumulative_ack = struct.unpack_from("<I", data, end)[0]
    else:
        cumulative_ack = None

    if flags & FLAG_HAS_CHECKSUM:
        end -= 4
        if end < 2:
            raise ValueError("short checksum footer")
    if flags & FLAG_INDEXED_CHANNEL:
        end -= 4
        if end < 2:
            raise ValueError("short channel footer")

    acks = ()
    if flags & FLAG_HAS_ACKS:
        end -= 1
        if end < 2:
            raise ValueError("short ack count")
        count = data[end]
        if count < 1:
            raise ValueError("empty ack list")
        end -= count * 4
        if end < 2:
            raise ValueError("short ack list")
        acks = struct.unpack_from("<" + "I" * count, data, end)

    sequence = None
    if flags & FLAG_HAS_SEQUENCE_NUMBER:
        end -= 4
        if end < 2:
            raise ValueError("short sequence footer")
        sequence = struct.unpack_from("<I", data, end)[0]

    if flags & FLAG_IS_FRAGMENT:
        raise ValueError("unsupported fragmented channel packet")

    first_request = None
    if flags & FLAG_HAS_REQUESTS:
        if end < 4:
            raise ValueError("short first request offset")
        end -= 2
        first_request = struct.unpack_from("<H", data, end)[0]

    if end < 2:
        raise ValueError("no message data")
    return ChannelPacket(flags, data[2:end], acks, cumulative_ack, sequence,
                         tuple(piggybacks), first_request)


def channel_packets(data):
    """Return attached packets in delivery order, followed by their parent."""
    packets = []
    pending = [data]
    while pending:
        packet = channel_packet(pending.pop())
        packets.append(packet)
        pending.extend(packet.piggybacks)
    packets.reverse()
    return packets
