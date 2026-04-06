from __future__ import annotations
import struct
from dataclasses import dataclass

MAGIC = b"VSTR"
VERSION = 1

_HEADER = struct.Struct("!4sBBHQI")
HEADER_SIZE = _HEADER.size

FLAG_KEYFRAME = 0x01

@dataclass(frozen=True)
class Header:
    version: int
    flags: int
    pts_us: int
    payload_len: int

def unpack_header(buf: bytes) -> Header:
    magic, ver, flags, _res, pts_us, payload_len = _HEADER.unpack(buf)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}")
    return Header(ver, flags, pts_us, payload_len)
