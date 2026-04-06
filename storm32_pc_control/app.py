from __future__ import annotations

import struct
import tkinter as tk
from tkinter import ttk, messagebox

import serial
from serial.tools import list_ports


class Storm32Controller:
    CMD_SETANGLE = 0x11
    SETANGLE_FLAGS = 0x05  # pitch + yaw limited
    SETANGLE_TYPE = 0x00

    def __init__(self) -> None:
        self.ser: serial.Serial | None = None
        self.pitch_deg = 0.0
        self.yaw_deg = 0.0

    def connect(self, port: str, baudrate: int = 115200) -> None:
        self.disconnect()
        self.ser = serial.Serial(port, baudrate, timeout=0.2)
        self.center()

    def disconnect(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _crc_x25(self, data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            tmp = byte ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (
                ((crc >> 8) & 0xFFFF)
                ^ ((tmp << 8) & 0xFFFF)
                ^ ((tmp << 3) & 0xFFFF)
                ^ ((tmp >> 4) & 0xFFFF)
            ) & 0xFFFF
        return crc

    def _send_setangle(self, pitch_deg: float, yaw_deg: float) -> None:
        if not self.connected():
            raise RuntimeError("Storm32 is not connected")

        payload = struct.pack(
            "<fffBB",
            float(pitch_deg),
            0.0,
            float(yaw_deg),
            self.SETANGLE_FLAGS,
            self.SETANGLE_TYPE,
        )
        frame_wo_crc = bytes((0xFA, len(payload), self.CMD_SETANGLE)) + payload
        crc = self._crc_x25(frame_wo_crc[1:])
        frame = frame_wo_crc + struct.pack("<H", crc)

        assert self.ser is not None
        print(f"[storm32_pc_control] tx CMD_SETANGLE pitch={pitch_deg:.1f} yaw={yaw_deg:.1f}", flush=True)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

    def move(self, dpitch: float = 0.0, dyaw: float = 0.0) -> tuple[float, float]:
        self.pitch_deg += dpitch
        self.yaw_deg += dyaw
        self._send_setangle(self.pitch_deg, self.yaw_deg)
        return self.pitch_deg, self.yaw_deg

    def center(self) -> tuple[float, float]:
        self.pitch_deg = 0.0
        self.yaw_deg = 0.0
        self._send_setangle(self.pitch_deg, self.yaw_deg)
        return self.pitch_deg, self.yaw_deg


class App(tk.Tk):
    PITCH_MIN = -90.0
    PITCH_MAX = 90.0
    YAW_MIN = -180.0
    YAW_MAX = 180.0

    def __init__(self) -> None:
        super().__init__()
        self.title("Storm32 PC Control")
        self.resizable(False, False)

        self.ctrl = Storm32Controller()

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.step_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Disconnected")
        self.pose_var = tk.StringVar(value="Pitch: 0.0   Yaw: 0.0")

        self._build_ui()
        self.refresh_ports()

        self.bind("<Left>", lambda _e: self.on_move(0.0, -self._step()))
        self.bind("<Right>", lambda _e: self.on_move(0.0, self._step()))
        self.bind("<Up>", lambda _e: self.on_move(self._step(), 0.0))
        self.bind("<Down>", lambda _e: self.on_move(-self._step(), 0.0))
        self.bind("<c>", lambda _e: self.on_center())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="nsew")

        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w", **pad)
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=16, state="readonly")
        self.port_box.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, **pad)

        ttk.Label(top, text="Baud").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.baud_var, width=18).grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(top, text="Step (deg)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.step_var, width=18).grid(row=2, column=1, sticky="ew", **pad)

        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=3, pady=(6, 8))
        ttk.Button(btns, text="Connect", command=self.on_connect).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="Disconnect", command=self.on_disconnect).grid(row=0, column=1, padx=6)
        ttk.Button(btns, text="Center (C)", command=self.on_center).grid(row=0, column=2, padx=6)

        pad_frame = ttk.LabelFrame(top, text="Move", padding=10)
        pad_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        ttk.Button(pad_frame, text="Up", width=10, command=lambda: self.on_move(self._step(), 0.0)).grid(row=0, column=1, pady=4)
        ttk.Button(pad_frame, text="Left", width=10, command=lambda: self.on_move(0.0, -self._step())).grid(row=1, column=0, padx=4, pady=4)
        ttk.Button(pad_frame, text="Center", width=10, command=self.on_center).grid(row=1, column=1, padx=4, pady=4)
        ttk.Button(pad_frame, text="Right", width=10, command=lambda: self.on_move(0.0, self._step())).grid(row=1, column=2, padx=4, pady=4)
        ttk.Button(pad_frame, text="Down", width=10, command=lambda: self.on_move(-self._step(), 0.0)).grid(row=2, column=1, pady=4)

        indicator_frame = ttk.LabelFrame(top, text="Angles", padding=10)
        indicator_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        indicator_frame.columnconfigure(1, weight=1)

        ttk.Label(indicator_frame, text="Pitch").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.pitch_scale = tk.Canvas(indicator_frame, width=260, height=28, highlightthickness=0, bg="#f3f4f6")
        self.pitch_scale.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(indicator_frame, text="Yaw").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.yaw_scale = tk.Canvas(indicator_frame, width=260, height=28, highlightthickness=0, bg="#f3f4f6")
        self.yaw_scale.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(top, textvariable=self.status_var).grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(top, textvariable=self.pose_var).grid(row=7, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(top, text="Keyboard: arrows move, C centers").grid(row=8, column=0, columnspan=3, sticky="w", **pad)

        self._draw_scale(self.pitch_scale, self.PITCH_MIN, self.PITCH_MAX, self.ctrl.pitch_deg, "Pitch")
        self._draw_scale(self.yaw_scale, self.YAW_MIN, self.YAW_MAX, self.ctrl.yaw_deg, "Yaw")

    def _step(self) -> float:
        try:
            return float(self.step_var.get())
        except ValueError as e:
            raise RuntimeError("Step must be a number") from e

    def refresh_ports(self) -> None:
        ports = [p.device for p in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        if not ports:
            self.port_var.set("")

    def on_connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Storm32", "Select a COM port first.")
            return

        try:
            baud = int(self.baud_var.get().strip())
            self.ctrl.connect(port, baud)
            self.status_var.set(f"Connected: {port} @ {baud}")
            self._update_pose()
            self.focus_force()
        except Exception as e:
            messagebox.showerror("Storm32", str(e))

    def on_disconnect(self) -> None:
        self.ctrl.disconnect()
        self.status_var.set("Disconnected")

    def on_move(self, dpitch: float, dyaw: float) -> None:
        try:
            self.ctrl.move(dpitch=dpitch, dyaw=dyaw)
            self._update_pose()
        except Exception as e:
            messagebox.showerror("Storm32", str(e))

    def on_center(self) -> None:
        try:
            self.ctrl.center()
            self._update_pose()
        except Exception as e:
            messagebox.showerror("Storm32", str(e))

    def _update_pose(self) -> None:
        self.pose_var.set(
            f"Pitch: {self.ctrl.pitch_deg:.1f}   Yaw: {self.ctrl.yaw_deg:.1f}"
        )
        self._draw_scale(self.pitch_scale, self.PITCH_MIN, self.PITCH_MAX, self.ctrl.pitch_deg, "Pitch")
        self._draw_scale(self.yaw_scale, self.YAW_MIN, self.YAW_MAX, self.ctrl.yaw_deg, "Yaw")

    def _draw_scale(
        self,
        canvas: tk.Canvas,
        min_value: float,
        max_value: float,
        current_value: float,
        label: str,
    ) -> None:
        canvas.delete("all")

        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        left = 12
        right = width - 12
        center_y = height // 2
        track_height = 8
        clamped = max(min_value, min(max_value, current_value))

        canvas.create_rectangle(left, center_y - track_height, right, center_y + track_height, fill="#d7dce2", outline="")
        zero_ratio = 0.0 if max_value == min_value else (0.0 - min_value) / (max_value - min_value)
        zero_x = left + zero_ratio * (right - left)
        canvas.create_line(zero_x, center_y - 12, zero_x, center_y + 12, fill="#475569", width=2)

        ratio = 0.0 if max_value == min_value else (clamped - min_value) / (max_value - min_value)
        marker_x = left + ratio * (right - left)
        canvas.create_oval(marker_x - 7, center_y - 7, marker_x + 7, center_y + 7, fill="#d9485f", outline="")

        canvas.create_text(left, 6, text=f"{min_value:.0f}", anchor="w", fill="#334155")
        canvas.create_text(right, 6, text=f"{max_value:.0f}", anchor="e", fill="#334155")
        canvas.create_text(width // 2, height - 4, text=f"{label}: {clamped:.1f}", anchor="s", fill="#0f172a")

    def on_close(self) -> None:
        self.ctrl.disconnect()
        self.destroy()


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
