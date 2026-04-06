from __future__ import annotations

import time
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .net import connect_tcp


class RobotTeleopWorker(QObject):
    """Maintains drive + gimbal TCP sockets and sends drive keepalive.

    Drive protocol (\\n-terminated):
      - V <v> W <w>   where v,w in [-1..1]
      - STOP

    Gimbal protocol (\\n-terminated):
      - PAN <step>
      - TILT <step>
      - CENTER
      - REBOOT

    This worker lives in its own QThread; all methods are slots called via queued signals.
    """

    log = pyqtSignal(str)
    gimbalState = pyqtSignal(float, float)
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._drive_sock = None
        self._gimbal_sock = None
        self._running = False

        self._v = 0.0
        self._w = 0.0

        self._keepalive_hz = 20
        self._timer: Optional[QTimer] = None
        self._pan = 0.0
        self._tilt = 0.0

    @pyqtSlot(str, int, int)
    def connect(self, host: str, drive_port: int, gimbal_port: int) -> None:
        if self._running:
            return
        self._running = True

        try:
            self._drive_sock = connect_tcp(host, drive_port)
            self.log.emit(f"[robot] drive connected to {host}:{drive_port}")
        except Exception as e:
            self.log.emit(f"[robot] drive connect error: {e}")
            self._running = False
            self.finished.emit()
            return

        try:
            self._gimbal_sock = connect_tcp(host, gimbal_port)
            self.log.emit(f"[robot] gimbal connected to {host}:{gimbal_port}")
            self._request_gimbal_state("GET")
        except Exception as e:
            self.log.emit(f"[robot] gimbal connect error: {e}")
            # still allow drive; gimbal can be optional
            self._gimbal_sock = None

        self._timer = QTimer()
        self._timer.setInterval(int(1000 / self._keepalive_hz))
        self._timer.timeout.connect(self._send_keepalive)
        self._timer.start()

    @pyqtSlot(float, float)
    def set_vw(self, v: float, w: float) -> None:
        self._v = max(-1.0, min(1.0, float(v)))
        self._w = max(-1.0, min(1.0, float(w)))

    @pyqtSlot()
    def stop_drive(self) -> None:
        self._v = 0.0
        self._w = 0.0
        #self._send_line(self._drive_sock, "STOP")

    @pyqtSlot(int)
    def gimbal_pan(self, step: int) -> None:
        self._send_gimbal_command(f"PAN {int(step)}")

    @pyqtSlot(int)
    def gimbal_tilt(self, step: int) -> None:
        self._send_gimbal_command(f"TILT {int(step)}")

    @pyqtSlot()
    def gimbal_center(self) -> None:
        self._send_gimbal_command("CENTER")

    @pyqtSlot()
    def gimbal_reboot(self) -> None:
        self._send_gimbal_command("REBOOT")

    def _send_keepalive(self) -> None:
        if not self._running:
            return
        self._send_line(self._drive_sock, f"V {self._v:.3f} W {self._w:.3f}")

    def _send_line(self, sock, line: str) -> None:
        if sock is None:
            return
        try:
            if not line.endswith("\n"):
                line += "\n"
            sock.sendall(line.encode("utf-8"))
            #print(line.encode("utf-8"))
        except Exception as e:
            self.log.emit(f"[robot] send error: {e}")

    def _recv_line(self, sock) -> Optional[str]:
        if sock is None:
            return None
        data = bytearray()
        try:
            while True:
                chunk = sock.recv(1)
                if not chunk:
                    raise OSError("gimbal disconnected")
                if chunk == b"\n":
                    break
                data.extend(chunk)
        except Exception as e:
            self.log.emit(f"[robot] gimbal recv error: {e}")
            return None
        return data.decode("utf-8", errors="ignore").strip()

    def _emit_gimbal_state(self, pan: float, tilt: float) -> None:
        self._pan = float(pan)
        self._tilt = float(tilt)
        self.gimbalState.emit(self._pan, self._tilt)

    def _handle_gimbal_reply(self, reply: str) -> None:
        if not reply:
            return
        parts = reply.split()
        if len(parts) < 3 or parts[0] != "OK":
            self.log.emit(f"[robot] gimbal reply: {reply}")
            return

        try:
            if parts[1] == "PAN" and len(parts) >= 3:
                self._emit_gimbal_state(float(parts[2]), self._tilt)
            elif parts[1] == "TILT" and len(parts) >= 3:
                self._emit_gimbal_state(self._pan, float(parts[2]))
            elif parts[1] == "CENTER" and len(parts) >= 4:
                self._emit_gimbal_state(float(parts[2]), float(parts[3]))
            elif parts[1] == "REBOOT" and len(parts) >= 4:
                self._emit_gimbal_state(float(parts[2]), float(parts[3]))
            elif parts[1] == "GET" and len(parts) >= 4:
                self._emit_gimbal_state(float(parts[2]), float(parts[3]))
            else:
                self.log.emit(f"[robot] gimbal reply: {reply}")
        except ValueError:
            self.log.emit(f"[robot] gimbal parse error: {reply}")

    def _request_gimbal_state(self, line: str) -> None:
        if self._gimbal_sock is None:
            return
        self._send_line(self._gimbal_sock, line)
        reply = self._recv_line(self._gimbal_sock)
        if reply is not None:
            self._handle_gimbal_reply(reply)

    def _send_gimbal_command(self, line: str) -> None:
        self._request_gimbal_state(line)

    @pyqtSlot()
    def disconnect(self) -> None:
        self._running = False

        try:
            if self._timer is not None:
                self._timer.stop()
        except Exception:
            pass

        # Always try to stop
        try:
            self._send_line(self._drive_sock, "STOP")
        except Exception:
            pass

        for s in (self._drive_sock, self._gimbal_sock):
            try:
                if s is not None:
                    try:
                        s.shutdown(2)
                    except Exception:
                        pass
                    s.close()
            except Exception:
                pass

        self._drive_sock = None
        self._gimbal_sock = None
        self._emit_gimbal_state(0.0, 0.0)

        self.log.emit("[robot] disconnected")
        self.finished.emit()


class TelemetryWorker(QObject):
    """Reads telemetry lines from robot and emits parsed dict."""

    log = pyqtSignal(str)
    telemetry = pyqtSignal(dict)
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
            self._sock.settimeout(0.5)
            self.log.emit(f"[telem] connected to {host}:{port}")
        except Exception as e:
            self.log.emit(f"[telem] connect error: {e}")
            self._running = False
            self.finished.emit()
            return

        buf = b""
        last = {}
        while self._running and self._sock is not None:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise OSError("telemetry disconnected")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    t = line.decode("utf-8", errors="ignore").strip()
                    if not t:
                        continue
                    k, *rest = t.split()
                    last[k] = " ".join(rest)
                # emit at most ~10 Hz (coalesce)
                self.telemetry.emit(last.copy())
            except TimeoutError:
                continue
            except Exception as e:
                self.log.emit(f"[telem] error: {e}")
                break

        self.disconnect()

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
        self.log.emit("[telem] disconnected")
        self.finished.emit()
