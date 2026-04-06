from __future__ import annotations
import os

from PyQt5 import uic
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QTransform
from PyQt5.QtWidgets import QLabel, QMainWindow, QMessageBox

import sip  # PyQt5's sip module provides isdeleted()

from .video_worker import VideoWorker
from .control_worker import ControlWorker
from .robot_worker import RobotTeleopWorker, TelemetryWorker
from .debug_player import DebugPlayer


def _alive(obj) -> bool:
    return obj is not None and not sip.isdeleted(obj)


class MainWindow(QMainWindow):
    # Use signals to invoke worker slots in their thread (QueuedConnection).
    startVideoRequested = pyqtSignal(str, int)
    connectControlRequested = pyqtSignal(str, int)
    sendControlRequested = pyqtSignal(str)

    # Robot teleop signals
    connectRobotRequested = pyqtSignal(str, int, int)  # host, drive_port, gimbal_port
    setVWRequested = pyqtSignal(float, float)
    stopDriveRequested = pyqtSignal()
    gimbalPanRequested = pyqtSignal(int)
    gimbalTiltRequested = pyqtSignal(int)
    gimbalCenterRequested = pyqtSignal()
    gimbalRebootRequested = pyqtSignal()

    connectTelemetryRequested = pyqtSignal(str, int)

    def __init__(self) -> None:
        super().__init__()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ui_mainwindow.ui"))
        uic.loadUi(ui_path, self)

        self._debug = DebugPlayer()

        self._video_thread: QThread | None = None
        self._video_worker: VideoWorker | None = None

        self._ctl_thread: QThread | None = None
        self._ctl_worker: ControlWorker | None = None

        self._robot_thread: QThread | None = None
        self._robot_worker: RobotTeleopWorker | None = None
        self._telem_thread: QThread | None = None
        self._telem_worker: TelemetryWorker | None = None

        self._keys_down: set[int] = set()
        self._robot_connected = False
        self._telem_last: dict = {}
        self._gimbal_pan = 0.0
        self._gimbal_tilt = 0.0
        self._video_overlay = QLabel(self.labelVideo)
        self._video_overlay.setObjectName("labelVideoOverlay")
        self._video_overlay.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._video_overlay.setStyleSheet(
            "QLabel { background: rgba(18, 33, 43, 185); color: white; "
            "border: 1px solid rgba(255,255,255,70); border-radius: 6px; "
            "padding: 8px 10px; font-weight: bold; }"
        )
        self._video_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._video_overlay.raise_()
        self._video_overlay.show()

        # Wire UI buttons
        self.btnConnectVideo.clicked.connect(self.on_connect_video)
        self.btnDisconnectVideo.clicked.connect(self.on_disconnect_video)
        self.btnStartDebug.clicked.connect(self.on_start_debug)
        self.btnStopDebug.clicked.connect(self.on_stop_debug)

        # "Control" tab in UI is repurposed for Robot Teleop in this variant.
        self.btnConnectControl.clicked.connect(self.on_connect_robot)
        self.btnDisconnectControl.clicked.connect(self.on_disconnect_robot)
        self.btnSendCommand.clicked.connect(self.on_send_command)  # keep raw sender if needed
        self.btnRebootGimbal.clicked.connect(self.on_reboot_gimbal)

        # Capture keys globally (WASD, arrows) when robot is connected.
        self.installEventFilter(self)
        self._update_telemetry_tab()
        self._update_gimbal_indicator()
        self._place_video_overlay()

    def host_ports(self):
        host = self.editHost.text().strip()
        v = int(self.editVideoPort.text().strip())
        drive = int(self.editControlPort.text().strip())
        gimbal = int(self.editGimbalPort.text().strip())
        telem = int(self.editTelemPort.text().strip())
        ffplay = int(self.editDebugPort.text().strip())
        return host, v, drive, gimbal, telem, ffplay

    def update_stats_line(self, video_text: str) -> None:
        # Combine video stats + telemetry on the same label.
        t = self._telem_last
        telem_parts = []

        # LOW BAT logic for 2S pack measured by INA219 *before* DC-DC.
        # Typical safe thresholds:
        #   warn  <= 6.8V, critical <= 6.6V (under load). Adjust if your pack chemistry differs.
        lowbat_tag = ""
        bat_v = None
        if "BAT" in t:
            try:
                bat_v = float(str(t["BAT"]))
            except Exception:
                bat_v = None
        if bat_v is not None:
            if bat_v <= 6.6:
                lowbat_tag = "CRIT BAT"
            elif bat_v <= 6.8:
                lowbat_tag = "LOW BAT"

        for k in ("MODE", "BAT", "RSSI", "TEMP"):
            if k in t:
                telem_parts.append(f"{k}:{t[k]}")
        if "BAT_PCT" in t:
            telem_parts.append(f"BAT_PCT:{t['BAT_PCT']}")
        if lowbat_tag:
            telem_parts.append(lowbat_tag)

        if telem_parts:
            self.labelStats.setText(video_text + "    |    " + "  ".join(telem_parts))
        else:
            self.labelStats.setText(video_text)

    def _set_low_bat_indicator(self, status: str, cells: str = "") -> None:
        status = (status or "").strip().upper()
        cells_text = cells if cells else "-"
        self.labelLowBatCellsValue.setText(cells_text)
        if status == "CRIT":
            self.labelLowBatIndicator.setText("BATTERY STATUS: CRIT BAT")
            self.labelLowBatIndicator.setStyleSheet("QLabel { background: #b71c1c; color: white; font-weight: bold; padding: 8px; border-radius: 4px; }")
        elif status == "YES":
            self.labelLowBatIndicator.setText("BATTERY STATUS: LOW BAT")
            self.labelLowBatIndicator.setStyleSheet("QLabel { background: #d32f2f; color: white; font-weight: bold; padding: 8px; border-radius: 4px; }")
        else:
            self.labelLowBatIndicator.setText("BATTERY STATUS: OK")
            self.labelLowBatIndicator.setStyleSheet("QLabel { background: #2f6f3e; color: white; font-weight: bold; padding: 8px; border-radius: 4px; }")

    def _update_telemetry_tab(self) -> None:
        t = self._telem_last or {}
        self.labelBat1Value.setText(str(t.get("BAT1", t.get("BAT", "-"))))
        self.labelBat2Value.setText(str(t.get("BAT2", "-")))
        self.labelBat3Value.setText(str(t.get("BAT3", "-")))
        self.labelBat4Value.setText(str(t.get("BAT4", "-")))
        self.labelBatVoltageValue.setText(str(t.get("BAT_V", "-")))
        self.labelBatCurrentValue.setText(str(t.get("BAT_I", "-")))
        self.labelBatPercentValue.setText(str(t.get("BAT_PCT", "-")))
        self.labelVbusVValue.setText(str(t.get("VBUS_V", "-")))
        self.labelVbusIValue.setText(str(t.get("VBUS_I", "-")))
        self.labelVbusPValue.setText(str(t.get("VBUS_P", "-")))
        self.labelTempValue.setText(str(t.get("TEMP", "-")))
        self._set_low_bat_indicator(str(t.get("LOW_BAT", "NO")), str(t.get("LOW_BAT_CELLS", "")))

    def _update_gimbal_indicator(self) -> None:
        self.labelGimbalPanValue.setText(f"{self._gimbal_pan:.1f} deg")
        self.labelGimbalTiltValue.setText(f"{self._gimbal_tilt:.1f} deg")
        self.labelGimbalState.setText(
            f"Gimbal pan {self._gimbal_pan:.1f} deg   |   tilt {self._gimbal_tilt:.1f} deg"
        )
        self._video_overlay.setText(
            f"PAN  {self._gimbal_pan:.1f} deg\nTILT {self._gimbal_tilt:.1f} deg"
        )
        self._video_overlay.adjustSize()
        self._place_video_overlay()

    def _place_video_overlay(self) -> None:
        margin = 12
        self._video_overlay.move(margin, margin)

    def log(self, s: str) -> None:
        self.textLog.append(s)

    # ---------- video ----------
    def on_connect_video(self) -> None:
        host, vport, _, _, _, _ = self.host_ports()
        self.on_disconnect_video()

        self._video_thread = QThread(self)
        self._video_worker = VideoWorker()
        self._video_worker.moveToThread(self._video_thread)

        # Connect signals -> slots (queued)
        self.startVideoRequested.connect(self._video_worker.start)
        self._video_worker.log.connect(self.log)
        self._video_worker.frameReady.connect(self.on_frame)
        self._video_worker.statsReady.connect(self.on_stats)

        # Cleanup wiring: when worker ends, stop thread; delete later is fine, but we must guard in disconnect.
        self._video_worker.finished.connect(self._video_thread.quit)
        self._video_worker.finished.connect(self._video_worker.deleteLater)
        self._video_thread.finished.connect(self._video_thread.deleteLater)

        self._video_thread.start()
        self.startVideoRequested.emit(host, vport)

        self.btnConnectVideo.setEnabled(False)
        self.btnDisconnectVideo.setEnabled(True)

    def on_disconnect_video(self) -> None:
        # Worker may have already finished and scheduled deletion; guard against deleted QThread wrappers.
        if _alive(self._video_worker):
            try:
                self._video_worker.stop()
            except Exception:
                pass

        if _alive(self._video_thread):
            try:
                self._video_thread.quit()
            except RuntimeError:
                # Underlying C++ already deleted
                pass
            try:
                self._video_thread.wait(1500)
            except RuntimeError:
                pass

        self._video_worker = None
        self._video_thread = None

        self.btnConnectVideo.setEnabled(True)
        self.btnDisconnectVideo.setEnabled(False)
        self.labelVideo.setText("No video")
        self.labelVideo.setPixmap(QPixmap())
        self.labelFpsValue.setText("-")
        self._video_overlay.raise_()
        self._place_video_overlay()

    def on_frame(self, img) -> None:
        pix = QPixmap.fromImage(img).transformed(QTransform().rotate(180), Qt.SmoothTransformation)
        self.labelVideo.setPixmap(
            pix.scaled(self.labelVideo.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self._video_overlay.raise_()
        self._place_video_overlay()

    def on_stats(self, st) -> None:
        self.labelFpsValue.setText(f"{st.fps:.1f}")
        self.update_stats_line(f"FPS: {st.fps:.1f}   Mbps: {st.mbps:.2f}   PTS(us): {st.last_pts_us}")

    # ---------- debug ----------
    def on_start_debug(self) -> None:
        host, _, _, _, _, dport = self.host_ports()
        try:
            self._debug.start_ffplay(host, dport)
            self.log(f"[debug] ffplay started on tcp://{host}:{dport}")
        except Exception as e:
            QMessageBox.critical(self, "Debug start error", str(e))

    def on_stop_debug(self) -> None:
        self._debug.stop()
        self.log("[debug] stopped")

    # ---------- control ----------
    def on_connect_control(self) -> None:
        """Legacy raw control channel (kept)."""
        host, _, cport, _, _, _ = self.host_ports()
        self.on_disconnect_control()

        self._ctl_thread = QThread(self)
        self._ctl_worker = ControlWorker()
        self._ctl_worker.moveToThread(self._ctl_thread)

        self.connectControlRequested.connect(self._ctl_worker.connect)
        self.sendControlRequested.connect(self._ctl_worker.send)

        self._ctl_worker.log.connect(self.textControlLog.append)
        self._ctl_worker.reply.connect(self.textControlLog.append)

        self._ctl_worker.finished.connect(self._ctl_thread.quit)
        self._ctl_worker.finished.connect(self._ctl_worker.deleteLater)
        self._ctl_thread.finished.connect(self._ctl_thread.deleteLater)

        self._ctl_thread.start()
        self.connectControlRequested.emit(host, cport)

        self.btnConnectControl.setEnabled(False)
        self.btnDisconnectControl.setEnabled(True)
        self.btnSendCommand.setEnabled(True)

    def on_disconnect_control(self) -> None:
        if _alive(self._ctl_worker):
            try:
                self._ctl_worker.disconnect()
            except Exception:
                pass

        if _alive(self._ctl_thread):
            try:
                self._ctl_thread.quit()
            except RuntimeError:
                pass
            try:
                self._ctl_thread.wait(1500)
            except RuntimeError:
                pass

        self._ctl_worker = None
        self._ctl_thread = None

        self.btnConnectControl.setEnabled(True)
        self.btnDisconnectControl.setEnabled(False)
        self.btnSendCommand.setEnabled(False)
        self.btnSendCommand.setEnabled(False)

    # ---------- robot teleop ----------
    def on_connect_robot(self) -> None:
        """Connect robot teleop: Drive (9001) + Gimbal (9002) + Telemetry (9003)."""
        host, _, drive_port, gimbal_port, telem_port, _ = self.host_ports()

        self.on_disconnect_robot()

        self._robot_thread = QThread(self)
        self._robot_worker = RobotTeleopWorker()
        self._robot_worker.moveToThread(self._robot_thread)

        self.connectRobotRequested.connect(self._robot_worker.connect)
        self.setVWRequested.connect(self._robot_worker.set_vw)
        self.stopDriveRequested.connect(self._robot_worker.stop_drive)
        self.gimbalPanRequested.connect(self._robot_worker.gimbal_pan)
        self.gimbalTiltRequested.connect(self._robot_worker.gimbal_tilt)
        self.gimbalCenterRequested.connect(self._robot_worker.gimbal_center)
        self.gimbalRebootRequested.connect(self._robot_worker.gimbal_reboot)
        self._robot_worker.gimbalState.connect(self.on_gimbal_state)

        self._robot_worker.log.connect(self.textControlLog.append)
        self._robot_worker.finished.connect(self._robot_thread.quit)
        self._robot_worker.finished.connect(self._robot_worker.deleteLater)
        self._robot_thread.finished.connect(self._robot_thread.deleteLater)

        self._robot_thread.start()
        self.connectRobotRequested.emit(host, drive_port, gimbal_port)

        # Telemetry
        self._telem_thread = QThread(self)
        self._telem_worker = TelemetryWorker()
        self._telem_worker.moveToThread(self._telem_thread)
        self.connectTelemetryRequested.connect(self._telem_worker.connect)
        self._telem_worker.log.connect(self.textControlLog.append)
        self._telem_worker.telemetry.connect(self.on_robot_telemetry)
        self._telem_worker.finished.connect(self._telem_thread.quit)
        self._telem_worker.finished.connect(self._telem_worker.deleteLater)
        self._telem_thread.finished.connect(self._telem_thread.deleteLater)

        self._telem_thread.start()
        self.connectTelemetryRequested.emit(host, telem_port)

        self._robot_connected = True
        self._keys_down.clear()

        self.btnConnectControl.setEnabled(False)
        self.btnDisconnectControl.setEnabled(True)
        # Raw command sender is legacy; keep disabled by default.
        self.btnSendCommand.setEnabled(False)

        self.textControlLog.append("[robot] WASD=drive, I/J/K/L=gimbal, SPACE=STOP, C=CENTER")
        self.textControlLog.append("[robot] use 'Hard reset gimbal' to pulse GPIO6 reset and recenter Storm32")

    def on_disconnect_robot(self) -> None:
        self._robot_connected = False
        self._keys_down.clear()

        if _alive(self._robot_worker):
            try:
                self._robot_worker.disconnect()
            except Exception:
                pass
        if _alive(self._robot_thread):
            try:
                self._robot_thread.quit()
            except RuntimeError:
                pass
            try:
                self._robot_thread.wait(1500)
            except RuntimeError:
                pass

        if _alive(self._telem_worker):
            try:
                self._telem_worker.disconnect()
            except Exception:
                pass
        if _alive(self._telem_thread):
            try:
                self._telem_thread.quit()
            except RuntimeError:
                pass
            try:
                self._telem_thread.wait(1500)
            except RuntimeError:
                pass

        self._robot_worker = None
        self._robot_thread = None
        self._telem_worker = None
        self._telem_thread = None
        self._gimbal_pan = 0.0
        self._gimbal_tilt = 0.0
        self._update_gimbal_indicator()

        self.btnConnectControl.setEnabled(True)
        self.btnDisconnectControl.setEnabled(False)

    def on_robot_telemetry(self, d: dict) -> None:
        self._telem_last = d
        self._update_telemetry_tab()
        cur = self.labelStats.text().split("    |    ")[0]
        self.update_stats_line(cur)

    def on_gimbal_state(self, pan: float, tilt: float) -> None:
        self._gimbal_pan = float(pan)
        self._gimbal_tilt = float(tilt)
        self._update_gimbal_indicator()

    def on_reboot_gimbal(self) -> None:
        self.textControlLog.append("[robot] hard resetting gimbal via GPIO6...")
        self.gimbalRebootRequested.emit()

    def eventFilter(self, obj, ev):
        if not self._robot_connected:
            return super().eventFilter(obj, ev)

        et = ev.type()
        if et in (ev.KeyPress, ev.KeyRelease):
            key = ev.key()
            is_press = (et == ev.KeyPress)

            # handle gimbal keys on press only
            if is_press:
                if key == Qt.Key_J:
                    self.gimbalPanRequested.emit(5)
                    return True
                if key == Qt.Key_L:
                    self.gimbalPanRequested.emit(-5)
                    return True
                if key == Qt.Key_I:
                    self.gimbalTiltRequested.emit(-5)
                    return True
                if key == Qt.Key_K:
                    self.gimbalTiltRequested.emit(5)
                    return True
                if key == Qt.Key_C:
                    self.gimbalCenterRequested.emit()
                    return True
                if key == Qt.Key_Space:
                    self.stopDriveRequested.emit()
                    self._keys_down.clear()
                    self.setVWRequested.emit(0.0, 0.0)
                    return True

            # track WASD state
            if key in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D):
                if is_press:
                    self._keys_down.add(key)
                else:
                    self._keys_down.discard(key)
                v = 0.0
                w = 0.0
                if Qt.Key_W in self._keys_down:
                    v += 1.0
                if Qt.Key_S in self._keys_down:
                    v -= 1.0
                if Qt.Key_A in self._keys_down:
                    w += 1.0
                if Qt.Key_D in self._keys_down:
                    w -= 1.0
                # If no keys, stop once
                if v == 0.0 and w == 0.0 and is_press is False:
                    self.stopDriveRequested.emit()
                self.setVWRequested.emit(v, w)
                return True

        return super().eventFilter(obj, ev)

    def on_send_command(self) -> None:
        line = self.editCommand.text().strip()
        self.textControlLog.append("> " + line)
        self.sendControlRequested.emit(line)

    def closeEvent(self, ev) -> None:
        try:
            self._debug.stop()
        except Exception:
            pass
        self.on_disconnect_video()
        self.on_disconnect_robot()
        self.on_disconnect_control()
        super().closeEvent(ev)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_video_overlay()
