# Raspberry Pi TCP Camera Server (Picamera2) — framed + debug port (SPS/PPS patch)

This build fixes late-join decoder errors like:
`non-existing PPS 0 referenced` (ffplay / VLC)

How:
- Cache latest **SPS (NAL type 7)** and **PPS (type 8)**
- When a new client connects (framed or debug), server:
  - **waits for next IDR (type 5)**,
  - then sends **SPS+PPS immediately before that IDR**,
  - then streams normally.

This makes connecting mid-stream reliable for `ffplay` on the debug port.

## Install
```bash
sudo apt update
sudo apt install -y python3-picamera2
```

## Run
```bash
cd server
python3 server.py --host 0.0.0.0 --video-port 9000 --control-port 9001 --debug-port 9002
```

## Debug (PC)
```bash
ffplay -fflags nobuffer -flags low_delay -framedrop -f h264 tcp://<PI_IP>:9002
```

## Ports
- 9000: framed (custom header + NAL payload)
- 9001: control (JSON lines)
- 9002: debug (raw H.264 bytestream)
