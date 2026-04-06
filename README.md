# Raspberry Pi Robot Control Stack

Python client-server project for a Raspberry Pi robot with:
- H.264 video streaming from CSI camera
- PC control client with PyQt5 UI
- robot drive control over TCP
- STorM32 gimbal control over TCP
- telemetry from the robot power system

This repository contains the current working project state used for a Raspberry Pi robot based on:
- Raspberry Pi + Picamera2
- OV5647 camera
- STorM32 gimbal controller
- motor controller over serial JSON protocol

## Repository Layout

```text
rpi_bot_2026/
  client_app/            PC client (PyQt5)
  docs/                  notes and architecture docs
  server/                Raspberry Pi video server
  storm32_pc_control/    separate minimal PC utility for direct STorM32 control
  robot_control_server.py
  run_robot_stack.sh
```

## Main Components

### 1. Video server on Raspberry Pi

Located in [server](server/).

Responsibilities:
- capture H.264 from Picamera2
- stream framed video to the PC client
- provide raw debug stream for `ffplay`

Default ports:
- `9000` video stream
- `9009` raw debug stream in the stack launcher

### 2. Robot control server on Raspberry Pi

Located in [robot_control_server.py](robot_control_server.py).

Responsibilities:
- drive control
- telemetry
- STorM32 gimbal control
- hard reset of the gimbal through `GPIO6`

Default ports:
- `9001` drive control
- `9002` gimbal control
- `9003` telemetry

### 3. PC client

Located in [client_app](client_app/).

Features:
- video view
- robot teleoperation
- gimbal control
- telemetry view
- on-screen gimbal pan/tilt overlay
- hard reset button for gimbal

Keyboard control:
- `W/A/S/D` drive
- `I/J/K/L` gimbal
- `Space` stop drive
- `C` gimbal center

## Quick Start

### Raspberry Pi

Install dependencies:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-serial python3-smbus python3-gpiozero
```

Run the stack:

```bash
chmod +x run_robot_stack.sh
./run_robot_stack.sh
```

### PC client

```bash
cd client_app
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
python run_client.py
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python run_client.py
```

## Gimbal Notes

The project uses `STorM32` over serial on Raspberry Pi.

Current defaults:
- device: `/dev/ttyACM0`
- baud: `115200`
- hard reset line: `GPIO6`

If your wiring differs, update the constants in [robot_control_server.py](robot_control_server.py).

## Video Notes

The client rotates the displayed video by `180` degrees.

The video server uses:
- framed H.264 stream for the main client
- raw H.264 debug stream for `ffplay`

## Publish Checklist

Before pushing to GitHub, verify:
- no local secrets or passwords are stored in files
- GPIO pin assignments match your hardware
- serial device names match your Raspberry Pi setup
- hostnames and ports in the UI are acceptable defaults

## Additional Docs

- [server/README.md](server/README.md)
- [client_app/README.md](client_app/README.md)
- [docs/README_v1.md](docs/README_v1.md)
