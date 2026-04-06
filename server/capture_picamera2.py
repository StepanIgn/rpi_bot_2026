from __future__ import annotations
import io
import threading
import queue
from dataclasses import dataclass
from typing import Optional, Callable

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FileOutput
except Exception as e:
    Picamera2 = None  # type: ignore
    H264Encoder = None  # type: ignore
    FileOutput = None  # type: ignore
    _import_error = e
else:
    _import_error = None

from h264_parser import split_buffer_to_nals

@dataclass
class CaptureConfig:
    width: int = 1280
    height: int = 720
    fps: int = 30
    bitrate: int = 2_000_000
    keyframe_period: int = 30
    rotation: int = 0

class QueueRawIO(io.RawIOBase):
    def __init__(self, q: "queue.Queue[bytes]") -> None:
        super().__init__()
        self.q = q

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:  # type: ignore[override]
        if b:
            self.q.put(b)
        return len(b)

class Picamera2Capture:
    def __init__(self, cfg: CaptureConfig, on_nal: Callable[[bytes], None], logger: Callable[[str], None]) -> None:
        self.cfg = cfg
        self.on_nal = on_nal
        self.log = logger

        self._stop = threading.Event()
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=400)
        self._parser_thread: Optional[threading.Thread] = None

        self.picam = None
        self.encoder = None
        self._buf_writer: Optional[io.BufferedWriter] = None
        self.output = None

    def start(self) -> None:
        if Picamera2 is None:
            raise RuntimeError(f"Picamera2 import failed: {_import_error}")

        if self.picam is not None:
            return

        self.picam = Picamera2()

        video_config = self.picam.create_video_configuration(
            main={"size": (self.cfg.width, self.cfg.height)},
            controls={"FrameRate": float(self.cfg.fps)},
        )
        self.picam.configure(video_config)

        try:
            self.encoder = H264Encoder(bitrate=int(self.cfg.bitrate), framerate=int(self.cfg.fps))
        except TypeError:
            self.encoder = H264Encoder(bitrate=int(self.cfg.bitrate))

        # If your Picamera2 supports repeating headers, enable it here if possible.
        # Some versions: encoder.repeat = True; others differ. It's OK if it fails —
        # the server also injects cached SPS/PPS on connect.
        try:
            self.encoder.repeat = True  # type: ignore[attr-defined]
        except Exception:
            pass

        raw = QueueRawIO(self._q)
        self._buf_writer = io.BufferedWriter(raw, buffer_size=4096)
        self.output = FileOutput(self._buf_writer)

        self.picam.start()
        self.picam.start_recording(self.encoder, self.output)

        self._stop.clear()
        self._parser_thread = threading.Thread(target=self._parse_loop, name="h264-parser", daemon=True)
        self._parser_thread.start()

        self.log("[capture] Picamera2 started")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.picam is not None:
                self.picam.stop_recording()
        except Exception:
            pass
        try:
            if self.picam is not None:
                self.picam.stop()
        except Exception:
            pass

        try:
            if self._buf_writer is not None:
                self._buf_writer.close()
        except Exception:
            pass

        self.picam = None
        self.encoder = None
        self.output = None
        self._buf_writer = None
        self.log("[capture] stopped")

    def _parse_loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not chunk:
                continue
            buf.extend(chunk)
            for nal in split_buffer_to_nals(buf):
                self.on_nal(nal)
