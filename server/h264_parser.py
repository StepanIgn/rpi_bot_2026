from __future__ import annotations
from typing import Iterator, Optional

def _find_start_code(buf: bytes, start: int) -> int:
    n = len(buf)
    i = start
    while i + 3 <= n:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1:
                return i
            if i + 4 <= n and buf[i + 2] == 0 and buf[i + 3] == 1:
                return i
        i += 1
    return -1

def split_buffer_to_nals(buf: bytearray) -> Iterator[bytes]:
    data = bytes(buf)
    first = _find_start_code(data, 0)
    if first == -1:
        if len(buf) > 8:
            del buf[:-8]
        return

    boundaries = []
    i = first
    while True:
        j = _find_start_code(data, i + 3)
        if j == -1:
            break
        boundaries.append((i, j))
        i = j

    for a, b in boundaries:
        yield data[a:b]

    del buf[:i]

def nal_type(nal_with_start_code: bytes) -> Optional[int]:
    if nal_with_start_code.startswith(b"\x00\x00\x00\x01"):
        off = 4
    elif nal_with_start_code.startswith(b"\x00\x00\x01"):
        off = 3
    else:
        return None
    if len(nal_with_start_code) <= off:
        return None
    return nal_with_start_code[off] & 0x1F
