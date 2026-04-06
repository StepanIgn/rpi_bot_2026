from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
import numpy as np

try:
    import av
except Exception as e:
    av = None
    _av_error = e
else:
    _av_error = None

@dataclass
class DecodeStats:
    fps: float
    mbps: float
    last_pts_us: int

class H264Decoder:
    """H.264 Annex-B bytestream decoder.

    IMPORTANT: H.264 frames are often split into multiple NAL units.
    Feeding single NALs directly to avcodec can produce 'Invalid data' errors.
    We therefore pass the byte stream through FFmpeg's H.264 parser via
    CodecContext.parse(), which yields complete packets (access units) suitable
    for decoding.
    """
    def __init__(self) -> None:
        if av is None:
            raise RuntimeError(f"PyAV import failed: {_av_error}")
        self._ctx = av.CodecContext.create("h264", "r")

    def decode(self, bytestream_chunk: bytes) -> Iterable[np.ndarray]:
        # bytestream_chunk can be any chunk of Annex-B data (one or more NALs).
        # parse() may yield 0..N packets.
        for pkt in self._ctx.parse(bytestream_chunk):
            for f in self._ctx.decode(pkt):
                yield f.to_rgb().to_ndarray()
