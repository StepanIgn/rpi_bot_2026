from __future__ import annotations
import time
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage

from .net import connect_tcp, recv_exact
from .protocol import HEADER_SIZE, unpack_header, VERSION
from .decoder import H264Decoder, DecodeStats

class VideoWorker(QObject):
    frameReady = pyqtSignal(QImage)
    statsReady = pyqtSignal(object)
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._sock = None
        self._dec: Optional[H264Decoder] = None

    @pyqtSlot(str, int)
    def start(self, host: str, port: int) -> None:
        if self._running:
            return
        self._running = True
        try:
            self._sock = connect_tcp(host, port)
            self._dec = H264Decoder()
            self.log.emit(f"[video] connected to {host}:{port}")
        except Exception as e:
            self.log.emit(f"[video] connect error: {e}")
            self._running = False
            self.finished.emit()
            return

        t0 = time.monotonic()
        bytes_acc = 0
        frames_acc = 0
        last_pts = 0

        try:
            while self._running:
                hdr_b = recv_exact(self._sock, HEADER_SIZE)
                hdr = unpack_header(hdr_b)
                if hdr.version != VERSION:
                    raise ValueError(f"unsupported version: {hdr.version}")
                payload = recv_exact(self._sock, hdr.payload_len)

                bytes_acc += HEADER_SIZE + hdr.payload_len
                last_pts = hdr.pts_us

                assert self._dec is not None
                for rgb in self._dec.decode(payload):
                    frames_acc += 1
                    h, w, _ = rgb.shape
                    img = QImage(rgb.data, w, h, 3*w, QImage.Format_RGB888)
                    self.frameReady.emit(img.copy())

                now = time.monotonic()
                dt = now - t0
                if dt >= 1.0:
                    mbps = (bytes_acc * 8.0) / 1_000_000.0 / dt
                    self.statsReady.emit(DecodeStats(frames_acc/dt, mbps, last_pts))
                    t0 = now
                    bytes_acc = 0
                    frames_acc = 0
        except Exception as e:
            if self._running:
                self.log.emit(f"[video] error: {e}")

        self._cleanup()
        self.finished.emit()

    @pyqtSlot()
    def stop(self) -> None:
        self._running = False

    def _cleanup(self) -> None:
        try:
            if self._sock is not None:
                try:
                    self._sock.shutdown(2)
                except Exception:
                    pass
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._dec = None
        self.log.emit("[video] disconnected")
