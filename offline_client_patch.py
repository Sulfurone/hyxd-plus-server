"""Prepare or restore a reversible call to the client's built-in offline mode."""

import argparse
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import shutil
import struct
import sys

from lz4.block import compress
from xdis.unmarshal import load_code, VersionIndependentUnmarshaller

from inspect_client_scripts import code_tree
from pgx_resources import decode_record


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "res/entities.pg"
BACKUP = ROOT / "_local_server/backups/entities.before-offline-start.pg"
GAME_OFFSET = 0x3252B8
NEXT_ENTRY = 0x329EB0


def encode_record(payload, target_size=None):
    compressed = compress(payload, mode="high_compression", compression=12,
                          store_size=False)
    if target_size is not None and len(compressed) + 4 != target_size:
        compressed = fit_compressed_size(compressed, payload, target_size - 4)
    cursor = 0
    output = bytearray(struct.pack("<I", len(payload)))
    while cursor < len(compressed):
        token = compressed[cursor]
        cursor += 1
        literal_count = token >> 4
        extensions = bytearray()
        if literal_count == 15:
            while True:
                extension = compressed[cursor]
                cursor += 1
                extensions.append(extension)
                literal_count += extension
                if extension != 255:
                    break
        final = cursor + literal_count == len(compressed)
        output.append(((token & 15) ^ 2) << 4 | (token >> 4))
        output.extend(extensions)
        mask = 0x14 if final else 0xD7
        output.extend(value ^ mask for value in compressed[cursor:cursor + literal_count])
        cursor += literal_count
        if final:
            break
        if literal_count > 65535:
            raise ValueError("Literal sequence exceeds the supported distance encoding")
        distance = struct.unpack_from("<H", compressed, cursor)[0]
        output.extend(struct.pack("<H", distance ^ 0x1129 ^ literal_count))
        cursor += 2
        if token & 15 == 15:
            while True:
                extension = compressed[cursor]
                cursor += 1
                output.append(extension)
                if extension != 255:
                    break
    return bytes(output)


def fit_compressed_size(compressed, payload, target_size):
    """Move a match suffix into the next literal run, preserving output bytes.

    The archive directory retains the original compressed byte count. The
    native decoder requires the final literal run to end at exactly that count.
    """
    sequences = []
    cursor = output_position = 0
    while cursor < len(compressed):
        token = compressed[cursor]
        cursor += 1
        literals = token >> 4
        if literals == 15:
            while True:
                extension = compressed[cursor]
                cursor += 1
                literals += extension
                if extension != 255:
                    break
        literal_bytes = compressed[cursor:cursor+literals]
        cursor += literals
        output_position += literals
        if cursor == len(compressed):
            sequences.append([literal_bytes, 0, 0, output_position])
            break
        distance = struct.unpack_from('<H', compressed, cursor)[0]
        cursor += 2
        count = (token & 15) + 4
        if count == 19:
            while True:
                extension = compressed[cursor]
                cursor += 1
                count += extension
                if extension != 255:
                    break
        output_position += count
        sequences.append([literal_bytes, distance, count, output_position])

    def length_bytes(value):
        return bytes([255]) * (value // 255) + bytes([value % 255])

    def encode_sequence(literal_bytes, distance, count):
        token = min(len(literal_bytes), 15) << 4
        if distance:
            token |= min(count - 4, 15)
        data = bytes([token])
        if len(literal_bytes) >= 15:
            data += length_bytes(len(literal_bytes) - 15)
        data += literal_bytes
        if distance:
            data += struct.pack('<H', distance)
            if count >= 19:
                data += length_bytes(count - 19)
        return data

    encoded = [encode_sequence(*s[:3]) for s in sequences]
    difference = target_size - len(compressed)
    if difference < 0:
        raise ValueError('Compressed resource exceeds the original directory length')
    for index, sequence in enumerate(sequences[:-1]):
        literals, distance, count, end = sequence
        next_literals, next_distance, next_count, _ = sequences[index+1]
        for cut in range(max(1, difference-8), min(count-4, difference+8)+1):
            left = encode_sequence(literals, distance, count-cut)
            right = encode_sequence(payload[end-cut:end]+next_literals, next_distance, next_count)
            if len(left)+len(right)-len(encoded[index])-len(encoded[index+1]) == difference:
                result = b''.join(encoded[:index]+[left,right]+encoded[index+2:])
                from lz4.block import decompress
                if len(result) != target_size or decompress(result, uncompressed_size=len(payload)) != payload:
                    raise ValueError('Fixed-length compression verification failed')
                return result
    raise ValueError('Cannot fit the original compressed resource length')


def redirect_offline_diagnostics(payload):
    class Positions(VersionIndependentUnmarshaller):
        def r_object(self, *args, **kwargs):
            begin = self.fp.tell()
            value = super().r_object(*args, **kwargs)
            self.spans[begin] = self.fp.tell()
            return value

    positions = Positions(BytesIO(payload), 62211, bytes_for_s=True)
    positions.spans = {}
    module = positions.load()
    function = next(code for code in code_tree(module) if code.co_name == 'offline')
    code_start = positions.code_to_file_offsets[function][1]
    constants_start = code_start + len(function.co_code)
    constants_end = positions.spans[constants_start]
    names_start = constants_end
    names_end = positions.spans[names_start]
    expression = ("execfile(" + repr(str(ROOT / '_local_server/offline_bootstrap.py').replace('\\', '/'))
                  + ", {'__name__': '_hyp_local_offline'})").encode('ascii')
    code = (b'\x67' + struct.pack('<H', len(function.co_names))
            + b'\x5f' + struct.pack('<H', len(function.co_consts))
            + bytes.fromhex('830100085f000047'))

    def append_tuple(begin, end, count, value):
        if payload[begin:begin+5] != b'(' + struct.pack('<I', count):
            raise ValueError('Unexpected diagnostics tuple encoding')
        return b'(' + struct.pack('<I', count+1) + payload[begin+5:end] + b's' + struct.pack('<I', len(value)) + value

    edits = [(code_start-5, constants_start, b's'+struct.pack('<I',len(code))+code),
             (constants_start, constants_end, append_tuple(constants_start, constants_end, len(function.co_consts), expression)),
             (names_start, names_end, append_tuple(names_start, names_end, len(function.co_names), b'eval'))]
    for begin, end, value in sorted(edits, reverse=True):
        payload = payload[:begin] + value + payload[end:]
    checked = load_code(BytesIO(payload), 62211, bytes_for_s=True)
    offline = next(c for c in code_tree(checked) if c.co_name == 'offline')
    if offline.co_code != code or offline.co_consts[-1] not in (expression, expression.decode('ascii')) or offline.co_names[-1] != 'eval':
        raise ValueError('Offline diagnostics verification failed')
    return payload


def prepare(archive):
    payload, end = decode_record(archive, GAME_OFFSET)
    module = load_code(BytesIO(payload), 62211, bytes_for_s=True)
    if module.co_filename != "/WORKSPACE/data/entities/client/game.o":
        raise ValueError("Unsupported client script resource")
    init = next(code for code in code_tree(module) if code.co_name == "start")
    if "offline" in init.co_names:
        raise ValueError("Offline startup patch is already present or the client is unsupported")
    if not init.co_code.endswith(bytes.fromhex("5f000047")):
        raise ValueError("Unexpected client initialization bytecode")
    # start() has created the UI. Queue offline() for the next engine tick,
    # after the native startup call has returned.
    name_index = len(init.co_names)
    call = (b"\x67" + struct.pack("<H", init.co_names.index("BigWorld"))
            + b"\x6d" + struct.pack("<H", init.co_names.index("callback"))
            + b"\x5f" + struct.pack("<H", init.co_consts.index(0))
            + b"\x67" + struct.pack("<H", name_index)
            + bytes.fromhex("83020008"))
    if call in init.co_code[-20:]:
        raise ValueError("Offline startup patch is already present")
    changed_code = init.co_code[:-4] + call + init.co_code[-4:]
    old = b"s" + struct.pack("<I", len(init.co_code)) + init.co_code
    new = b"s" + struct.pack("<I", len(changed_code)) + changed_code
    if payload.count(old) != 1:
        raise ValueError("Initialization code is not uniquely identifiable")
    class Positions(VersionIndependentUnmarshaller):
        def r_object(self, *args, **kwargs):
            begin = self.fp.tell()
            value = super().r_object(*args, **kwargs)
            if value == init.co_names:
                self.names_position = (begin, self.fp.tell())
            return value

    positions = Positions(BytesIO(payload), 62211, bytes_for_s=True)
    positions.load()
    begin, end_names = positions.names_position
    if payload[begin:begin + 5] != b"(" + struct.pack("<I", name_index):
        raise ValueError("Unexpected startup name tuple encoding")
    names = (b"(" + struct.pack("<I", name_index + 1)
             + payload[begin + 5:end_names] + b"s\x07\x00\x00\x00offline")
    changed_payload = (payload[:begin] + names + payload[end_names:]).replace(old, new, 1)
    checked_module = load_code(BytesIO(changed_payload), 62211, bytes_for_s=True)
    checked_init = next(code for code in code_tree(checked_module) if code.co_name == "start")
    if checked_init.co_code != changed_code or checked_init.co_names != init.co_names + ("offline",):
        raise ValueError("Modified script failed marshal verification")
    changed_payload = redirect_offline_diagnostics(changed_payload)
    record = encode_record(changed_payload, end - GAME_OFFSET)
    if len(record) > NEXT_ENTRY - GAME_OFFSET:
        raise ValueError("Modified script exceeds its allocated resource slot")
    changed_archive = bytearray(archive)
    changed_archive[GAME_OFFSET:GAME_OFFSET + len(record)] = record
    decoded, _ = decode_record(changed_archive, GAME_OFFSET)
    if decoded != changed_payload:
        raise ValueError("Modified resource failed compression verification")
    return bytes(changed_archive), len(record), end - GAME_OFFSET


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        if not BACKUP.is_file():
            raise ValueError("Original resource backup is missing")
        shutil.copy2(BACKUP, ARCHIVE)
        print("Restored original entities resource")
    else:
        original = ARCHIVE.read_bytes()
        if args.apply and BACKUP.is_file() and original != BACKUP.read_bytes():
            expected, _, _ = prepare(BACKUP.read_bytes())
            if original == expected:
                print('Verified offline startup patch is already installed')
                sys.exit(0)
            raise ValueError('Current resource differs from both the original and this offline patch')
        changed, size, previous_size = prepare(original)
        print("Verified offline startup patch")
        print("Compressed resource bytes", previous_size, "->", size)
        print("Original archive SHA-256", sha256(original).hexdigest())
        if args.apply:
            BACKUP.parent.mkdir(exist_ok=True)
            if BACKUP.exists() and BACKUP.read_bytes() != original:
                raise ValueError("Existing backup differs from the current original resource")
            if not BACKUP.exists():
                shutil.copy2(ARCHIVE, BACKUP)
            temporary = ARCHIVE.with_suffix(".offline.tmp")
            temporary.write_bytes(changed)
            temporary.replace(ARCHIVE)
            print("Applied offline startup patch, backup", BACKUP)
