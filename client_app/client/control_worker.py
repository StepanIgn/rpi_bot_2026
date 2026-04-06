from __future__ import annotations
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from .net import connect_tcp

class ControlWorker(QObject):
    log = pyqtSignal(str)
    reply = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._sock = None
        self._running = False

    @pyqtSlot(str, int)
    def connect(self, host: str, port: int) -> None:
        if self._running:
            return
        self._running = True
        try:
            self._sock = connect_tcp(host, port)
            self.log.emit(f"[control] connected to {host}:{port}")
        except Exception as e:
            self.log.emit(f"[control] connect error: {e}")
            self._running = False
            self.finished.emit()

    @pyqtSlot(str)
    def send(self, line: str) -> None:
        if not self._running or self._sock is None:
            self.log.emit("[control] not connected")
            return
        if not line.endswith("\n"):
            line += "\n"
        try:
            self._sock.sendall(line.encode("utf-8"))
            data = self._sock.recv(65536)
            if data:
                for ln in data.decode("utf-8", errors="replace").splitlines():
                    if ln.strip():
                        self.reply.emit(ln)
        except Exception as e:
            self.log.emit(f"[control] error: {e}")

    @pyqtSlot()
    def disconnect(self) -> None:
        self._running = False
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
        self.log.emit("[control] disconnected")
        self.finished.emit()
