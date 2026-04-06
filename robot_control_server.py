import asyncio
import json
import os
import queue
import struct
import threading
import time
import serial
import smbus

try:
    from gpiozero import OutputDevice
except Exception:
    OutputDevice = None  # type: ignore

# ===== CONFIG =====
DRIVE_PORT = 9001
GIMBAL_PORT = 9002
TELEMETRY_PORT = 9003

SERIAL_DEV = "/dev/serial0"
SERIAL_BAUD = 115200
STORM32_DEV = os.environ.get("STORM32_DEV", "/dev/ttyACM0")
STORM32_BAUD = int(os.environ.get("STORM32_BAUD", "115200"))
STORM32_REPLY_TIMEOUT = 0.15
GIMBAL_RESET_PIN = 6
GIMBAL_RESET_PULSE_SEC = 0.15

# моторы
LEFT_ID = 4
RIGHT_ID = 3

LEFT_SIGN = 1
RIGHT_SIGN = -1

MAX_CMD = 200
MIN_CMD = 0
ACT = 2
CMD_TYPE_DDSM115 = {"T": 11002, "type": 115}

# управление
CONTROL_HZ = 50
DEADBAND = 0.04

V_ACCEL_STEP = 0.01
V_BRAKE_STEP = 0.01

W_ACCEL_STEP = 0.01
W_BRAKE_STEP = 0.01

# UART
SERIAL_REPLY_TIMEOUT = 0.15
SERIAL_READ_TIMEOUT = 0.02

# safety
DEADMAN_SEC = 0.5

# UPS
ADDR = 0x2d
bus = smbus.SMBus(1)


# ==========================
def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def apply_deadband(x, threshold):
    return 0.0 if abs(x) < threshold else x


def approach(current, target, up, down):
    if target > current:
        return min(current + up, target)
    if target < current:
        return max(current - down, target)
    return current


def scale_to_cmd(x):
    if abs(x) < 1e-6:
        return 0
    sign = 1 if x > 0 else -1
    mag = abs(x)
    cmd = MIN_CMD + mag * (MAX_CMD - MIN_CMD)
    return int(sign * cmd)


# ==========================
class RobotState:
    def __init__(self):
        self.target_v = 0.0
        self.target_w = 0.0
        self.current_v = 0.0
        self.current_w = 0.0
        self.last_cmd = time.time()


# ==========================
class GimbalState:
    def __init__(self):
        self.pan = 90
        self.tilt = 90
        self.lock = threading.Lock()


class GimbalIO:
    """Drives a STorM32 gimbal over its serial RC protocol."""

    PAN_MIN = -135.0
    PAN_MAX = 135.0
    TILT_MIN = -90.0
    TILT_MAX = 45.0
    PAN_CENTER = 0.0
    TILT_CENTER = 0.0
    CMD_SETANGLE = 0x11
    SETANGLE_FLAGS = 0x05  # pitch + yaw in limited mode; roll unused
    SETANGLE_TYPE = 0x00

    def __init__(self):
        self.state = GimbalState()
        self.state.pan = int(self.PAN_CENTER)
        self.state.tilt = int(self.TILT_CENTER)
        self.ser = None
        self.reset_line = None
        self._init_reset_line()
        self._open_serial()

    def _init_reset_line(self):
        if OutputDevice is None:
            print("[gimbal] gpiozero unavailable; hard reset disabled", flush=True)
            return
        try:
            # STorM32 reset is assumed active-low on GPIO6.
            self.reset_line = OutputDevice(GIMBAL_RESET_PIN, active_high=False, initial_value=False)
            print(f"[gimbal] reset line ready on GPIO{GIMBAL_RESET_PIN}", flush=True)
        except Exception as e:
            self.reset_line = None
            print(f"[gimbal] reset line unavailable: {e}", flush=True)

    def _open_serial(self):
        try:
            self.ser = serial.Serial(STORM32_DEV, STORM32_BAUD, timeout=STORM32_REPLY_TIMEOUT)
            print(f"[gimbal] storm32 connected on {STORM32_DEV} @ {STORM32_BAUD}", flush=True)
        except Exception as e:
            self.ser = None
            print(f"[gimbal] storm32 serial unavailable: {e}", flush=True)

    def _clamp(self, value, lo, hi):
        return lo if value < lo else hi if value > hi else value

    def close(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def reboot(self):
        self.close()
        if self.reset_line is None:
            raise RuntimeError("storm32 hard reset unavailable on GPIO6")
        self.reset_line.on()
        time.sleep(GIMBAL_RESET_PULSE_SEC)
        self.reset_line.off()
        time.sleep(1.0)
        self._open_serial()
        if self.ser is None:
            raise RuntimeError("storm32 reopen failed")
        return self.center()

    def _crc_x25(self, data):
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

    def _send_setangle(self, pitch_deg, yaw_deg):
        if self.ser is None:
            print(f"[gimbal] offline pitch={pitch_deg:.1f} yaw={yaw_deg:.1f}", flush=True)
            return

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

        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

        reply = self.ser.read(6)
        if len(reply) < 6:
            print("[gimbal] storm32 ack timeout", flush=True)
            return

        if reply[0] != 0xFB or reply[2] != 0x96 or reply[3] != 0:
            print(f"[gimbal] storm32 ack unexpected: {reply.hex()}", flush=True)

    def pan(self, delta):
        with self.state.lock:
            self.state.pan = self._clamp(self.state.pan + float(delta), self.PAN_MIN, self.PAN_MAX)
            angle = self.state.pan
            pitch = self.state.tilt
        self._send_setangle(pitch, angle)
        return angle

    def tilt(self, delta):
        with self.state.lock:
            self.state.tilt = self._clamp(self.state.tilt + float(delta), self.TILT_MIN, self.TILT_MAX)
            angle = self.state.tilt
            yaw = self.state.pan
        self._send_setangle(angle, yaw)
        return angle

    def center(self):
        with self.state.lock:
            self.state.pan = self.PAN_CENTER
            self.state.tilt = self.TILT_CENTER
            pan = self.state.pan
            tilt = self.state.tilt
        self._send_setangle(tilt, pan)
        return pan, tilt

    def snapshot(self):
        with self.state.lock:
            return self.state.pan, self.state.tilt


# ==========================
class SerialWorker:
    def __init__(self):
        self.ser = serial.Serial(SERIAL_DEV, SERIAL_BAUD, timeout=SERIAL_READ_TIMEOUT)

        self.control_q = queue.Queue()

        self._speed_lock = threading.Lock()
        self._latest_speed = None
        self._speed_dirty = False

        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self._stop.set()
        self.thread.join(timeout=1)
        self.ser.close()

    def send_control(self, obj):
        self.control_q.put(obj)

    def set_speed(self, left, right):
        with self._speed_lock:
            self._latest_speed = (
                {"T": 10010, "id": LEFT_ID, "cmd": int(left), "act": ACT},
                {"T": 10010, "id": RIGHT_ID, "cmd": int(right), "act": ACT},
            )
            self._speed_dirty = True

    def _pop_speed(self):
        with self._speed_lock:
            if not self._speed_dirty:
                return None
            self._speed_dirty = False
            return self._latest_speed

    def _send_and_wait(self, obj):
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self.ser.write(line.encode())
        self.ser.flush()

        deadline = time.time() + SERIAL_REPLY_TIMEOUT

        while time.time() < deadline:
            data = self.ser.read(256)
            if data:
                return  # любое подтверждение
            time.sleep(0.002)

    def _run(self):
        while not self._stop.is_set():
            try:
                obj = self.control_q.get(timeout=0.02)
                self._send_and_wait(obj)
                continue
            except queue.Empty:
                pass

            pair = self._pop_speed()
            if pair:
                self._send_and_wait(pair[0])
                self._send_and_wait(pair[1])
                continue

            time.sleep(0.005)


# ==========================
class RobotIO:
    def __init__(self):
        self.worker = SerialWorker()
        self.worker.start()

    def init(self):
        self.worker.send_control(CMD_TYPE_DDSM115)
        self.worker.send_control({"T": 11002, "id": LEFT_ID})
        self.worker.send_control({"T": 11002, "id": RIGHT_ID})

        self.worker.send_control({"T": 10012, "id": LEFT_ID, "mode": 2})
        self.worker.send_control({"T": 10012, "id": RIGHT_ID, "mode": 2})

    def set_speed(self, left, right):
        self.worker.set_speed(left, right)

    def stop(self):
        self.set_speed(0, 0)


# ==========================
def read_ups():
    data = bus.read_i2c_block_data(ADDR, 0x30, 0x08)

    v1 = (data[0] | data[1] << 8) / 1000.0
    v2 = (data[2] | data[3] << 8) / 1000.0
    v3 = (data[4] | data[5] << 8) / 1000.0
    v4 = (data[6] | data[7] << 8) / 1000.0

    cells = [v1, v2, v3, v4]

    low = [i + 1 for i, v in enumerate(cells) if v <= 3.40]
    crit = [i + 1 for i, v in enumerate(cells) if v <= 3.20]

    if crit:
        status = "CRIT"
    elif low:
        status = "YES"
    else:
        status = "NO"

    return cells, status, low

def read_vbus():
    data = bus.read_i2c_block_data(ADDR, 0x10, 0x06)

    vbus_mv = (data[0] | (data[1] << 8))
    vbus_ma = (data[2] | (data[3] << 8))
    vbus_mw = (data[4] | (data[5] << 8))
    if(vbus_ma > 0x7FFF):
        vbus_ma -= 0xFFFF

    return vbus_mv, vbus_ma, vbus_mw

def read_battery():
    try:
        data = bus.read_i2c_block_data(ADDR, 0x20, 0x0C)

        voltage = (data[0] | (data[1] << 8))  # mV

        current = (data[2] | (data[3] << 8))
        if current > 0x7FFF:
            current -= 0xFFFF  # signed

        percent = int(data[4] | (data[5] << 8))

        return voltage, current, percent

    except Exception:
        return 0, 0, 0

def get_temp():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read()) / 1000.0


# ==========================
async def telemetry_server():
    async def handle(reader, writer):
        print("[telemetry] connected")

        try:
            while True:
                cells, status, low_cells = read_ups()
                vbus_mv, vbus_ma, vbus_mw = read_vbus()
                temp = get_temp()
                bat_mv, bat_ma, bat_pct = read_battery()
                writer.write(b"MODE TELEOP\n")

                for i, v in enumerate(cells):
                    writer.write(f"BAT{i+1} {v:.3f} V\n".encode())

                writer.write(f"VBUS_V {vbus_mv} mV\n".encode())
                writer.write(f"VBUS_I {vbus_ma} mA\n".encode())
                writer.write(f"VBUS_P {vbus_mw} mW\n".encode())
                writer.write(f"BAT_V {bat_mv} mV\n".encode())
                writer.write(f"BAT_I {bat_ma} mA\n".encode())
                writer.write(f"BAT_PCT {bat_pct} %\n".encode())

                writer.write(f"LOW_BAT {status}\n".encode())

                if low_cells:
                    writer.write(f"LOW_BAT_CELLS {','.join(map(str, low_cells))}\n".encode())

                writer.write(f"TEMP {temp:.1f} C\n".encode())

                await writer.drain()
                await asyncio.sleep(0.5)

        finally:
            print("[telemetry] disconnected")
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", TELEMETRY_PORT)
    async with server:
        await server.serve_forever()


# ==========================
async def drive_server(state, rio):
    async def handle(reader, writer):
        print("[drive] connected")

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                s = line.decode().strip()

                if s == "STOP":
                    state.target_v = 0
                    state.target_w = 0
                    rio.stop()
                    continue

                parts = s.split()
                if len(parts) == 4:
                    state.target_v = float(parts[1])
                    state.target_w = float(parts[3])
                    state.last_cmd = time.time()

        finally:
            print("[drive] disconnected")
            rio.stop()

    server = await asyncio.start_server(handle, "0.0.0.0", DRIVE_PORT)
    async with server:
        await server.serve_forever()


# ==========================
async def gimbal_server(gimbal):
    async def handle(reader, writer):
        print("[gimbal] connected")

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                s = line.decode().strip()
                if not s:
                    continue

                parts = s.split()
                cmd = parts[0].upper()

                if cmd == "PAN" and len(parts) == 2:
                    angle = gimbal.pan(int(parts[1]))
                    writer.write(f"OK PAN {angle}\n".encode())
                elif cmd == "TILT" and len(parts) == 2:
                    angle = gimbal.tilt(int(parts[1]))
                    writer.write(f"OK TILT {angle}\n".encode())
                elif cmd == "CENTER" and len(parts) == 1:
                    pan, tilt = gimbal.center()
                    writer.write(f"OK CENTER {pan} {tilt}\n".encode())
                elif cmd == "REBOOT" and len(parts) == 1:
                    pan, tilt = gimbal.reboot()
                    writer.write(f"OK REBOOT {pan} {tilt}\n".encode())
                elif cmd == "GET" and len(parts) == 1:
                    pan, tilt = gimbal.snapshot()
                    writer.write(f"OK GET {pan} {tilt}\n".encode())
                else:
                    writer.write(b"ERR bad command\n")

                await writer.drain()

        finally:
            print("[gimbal] disconnected")
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", GIMBAL_PORT)
    async with server:
        await server.serve_forever()


# ==========================
async def control_loop(state, rio):
    dt = 1.0 / CONTROL_HZ

    while True:
        if time.time() - state.last_cmd > DEADMAN_SEC:
            state.target_v = 0
            state.target_w = 0

        state.current_v = approach(state.current_v, state.target_v, V_ACCEL_STEP, V_BRAKE_STEP)
        state.current_w = approach(state.current_w, state.target_w, W_ACCEL_STEP, W_BRAKE_STEP)

        left = clamp(state.current_v - state.current_w, -1, 1)
        right = clamp(state.current_v + state.current_w, -1, 1)

        left = apply_deadband(left, DEADBAND)
        right = apply_deadband(right, DEADBAND)

        left_cmd = scale_to_cmd(left) * LEFT_SIGN
        right_cmd = scale_to_cmd(right) * RIGHT_SIGN

        rio.set_speed(left_cmd, right_cmd)

        await asyncio.sleep(dt)


# ==========================
async def main():
    rio = RobotIO()
    gimbal = GimbalIO()
    state = RobotState()

    try:
        print("[init]")
        rio.init()
        gimbal.center()

        await asyncio.gather(
            drive_server(state, rio),
            gimbal_server(gimbal),
            telemetry_server(),
            control_loop(state, rio),
        )
    finally:
        gimbal.close()


if __name__ == "__main__":
    asyncio.run(main())
