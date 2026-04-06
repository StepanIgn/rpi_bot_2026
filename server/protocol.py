from __future__ import annotations
from dataclasses import dataclass
import struct

MAGIC = b"VSTR"
VERSION = 1

_HEADER = struct.Struct("!4sBBHQI")
HEADER_SIZE = _HEADER.size

FLAG_KEYFRAME = 0x01

@dataclass(frozen=True)
class VideoHeader:
    version: int
    flags: int
    pts_us: int
    payload_len: int

def pack_header(flags: int, pts_us: int, payload_len: int, version: int = VERSION) -> bytes:
    return _HEADER.pack(MAGIC, version, flags, 0, pts_us, payload_len)
