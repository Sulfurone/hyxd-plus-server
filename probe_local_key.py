"""Read the local 3.0.43 test client's cipher key; never write client memory.

Requires administrator rights because the original game runs elevated. This is
a temporary integration bridge while RSA resource overrides are investigated.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import struct
import time

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent / "game" / "hyp_beta.exe"
EXPECTED = "3a28fae6cb38d51ef1d3a84549ad46b61fa0aa909f8303952f7f528da2077f71"
kernel = c.WinDLL("kernel32", use_last_error=True)
psapi = c.WinDLL("psapi", use_last_error=True)
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t,
                                     c.POINTER(c.c_size_t)]
kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
kernel.CloseHandle.argtypes = [w.HANDLE]
psapi.EnumProcessModulesEx.argtypes = [w.HANDLE, c.POINTER(c.c_void_p), w.DWORD,
                                     c.POINTER(w.DWORD), w.DWORD]


class Region(c.Structure):
    _fields_ = [("base", c.c_void_p), ("allocation", c.c_void_p),
                ("allocation_protect", w.DWORD), ("partition", w.WORD),
                ("size", c.c_size_t), ("state", w.DWORD),
                ("protect", w.DWORD), ("kind", w.DWORD)]


kernel.VirtualQueryEx.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(Region), c.c_size_t]
kernel.VirtualQueryEx.restype = c.c_size_t


def read(handle, address, size):
    buffer = c.create_string_buffer(size)
    count = c.c_size_t()
    if not kernel.ReadProcessMemory(handle, address, buffer, size, c.byref(count)):
        raise c.WinError(c.get_last_error())
    if count.value != size:
        raise ValueError("partial read")
    return buffer.raw


def capture(pid):
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        name = c.create_unicode_buffer(32768)
        length = w.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, c.byref(length)):
            raise c.WinError(c.get_last_error())
        if name.value and Path(name.value).resolve() != CLIENT.resolve():
            raise ValueError("Unexpected process identity")
        modules = (c.c_void_p * 1024)()
        needed = w.DWORD()
        if not psapi.EnumProcessModulesEx(handle, modules, c.sizeof(modules),
                                         c.byref(needed), 3):
            raise c.WinError(c.get_last_error())
        signature = struct.pack("<Q", modules[0] + 0x1a5c918)
        position = 0
        stats = {"regions": 0, "bytes": 0, "hits": 0, "signature": signature.hex()}
        while position < 0x7ffffffeffff:
            region = Region()
            if not kernel.VirtualQueryEx(handle, position, c.byref(region), c.sizeof(region)):
                break
            base = region.base or 0
            position = base + region.size
            stats["regions"] += 1
            if (region.state != 0x1000 or region.kind != 0x20000
                    or region.protect & 0x101 or not region.protect & 0xee
                    or region.size > 64 * 1024 * 1024):
                continue
            try:
                data = read(handle, base, region.size)
                stats["bytes"] += len(data)
            except OSError:
                continue
            start = 0
            while True:
                offset = data.find(signature, start)
                if offset < 0:
                    break
                start = offset + 8
                stats["hits"] += 1
                try:
                    header = read(handle, base + offset, 64)
                    size, capacity = struct.unpack_from("<QQ", header, 32)
                    declared = struct.unpack_from("<I", header, 48)[0]
                    if not 4 <= size <= 56 or size != declared or header[52] not in (0, 1):
                        continue
                    key = (header[16:16 + size] if capacity < 16 else
                           read(handle, struct.unpack_from("<Q", header, 16)[0], size))
                    return key
                except (OSError, ValueError):
                    continue
        (HERE / "logs" / "key_probe_scan.json").write_text(json.dumps(stats), encoding="utf-8")
        return None
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if hashlib.sha256(CLIENT.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError("Unsupported client build")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        key = capture(args.pid)
        if key:
            path = HERE / "logs" / "client_session.bin"
            path.with_suffix(".tmp").write_bytes(key)
            path.with_suffix(".tmp").replace(path)
            (HERE / "logs" / "client_session.json").write_text(
                json.dumps({"pid": args.pid, "key_bytes": len(key),
                            "captured": time.time()}), encoding="utf-8")
            print("Captured local test session (%d bytes)" % len(key), flush=True)
            return
        time.sleep(0.5)
    raise TimeoutError("Local cipher object not available")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        (HERE / "logs" / "key_probe_error.json").write_text(
            json.dumps({"time": time.time(), "type": type(error).__name__,
                        "message": str(error)}), encoding="utf-8")
        raise
