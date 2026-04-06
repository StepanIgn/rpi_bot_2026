# TCP Camera v1.0 — Architecture & Constraints

## Components

### Server (Raspberry Pi)
- **Capture (Picamera2)**: camera + H.264 encoder, outputs Annex-B bytestream.
- **NAL splitter**: splits bytestream into NAL units (start codes `00 00 01` / `00 00 00 01`).
- **SPS/PPS cache + start-on-IDR**:
  - caches last SPS (type 7) and PPS (type 8)
  - for a new client: waits for next IDR (type 5) and injects SPS+PPS before first IDR
- **TCP ports**:
  - `9000` framed video (header + H.264 payload)
  - `9001` control (JSON lines)
  - `9002` debug raw H.264 (Annex-B)

### Client (PC, PyQt5)
- **GUI thread**: only UI and rendering.
- **VideoWorker (QThread)**:
  - reads framed stream
  - decodes using **PyAV + FFmpeg parser (`CodecContext.parse`)**
  - emits frames to GUI via Qt signals
- **ControlWorker (QThread)**: JSON-lines commands to control port.
- **Debug**: `ffplay` can connect to debug port.

## Dataflow
Picamera2 → H.264 bytestream → NAL split → (cache SPS/PPS) → TCP send  
Client: TCP recv → payload bytes → FFmpeg parser → decode → QImage → GUI

## Key invariant (why it works reliably)
Decoder is started only on a valid keyframe boundary:
- server waits for IDR
- server injects SPS/PPS before first IDR per connection
This prevents “non-existing PPS referenced” and black screen on reconnect.

## Constraints / Non-goals (v1.0)
- One active client per port (no fan-out/multicast).
- TCP can accumulate latency if the client cannot decode fast enough.
- Not intended for WAN or high-jitter networks.
- Client depends on FFmpeg/PyAV availability on the PC.

## Quick diagnostics
1) Check server output with ffplay (debug port):
```bash
ffplay -fflags nobuffer -flags low_delay -framedrop -f h264 tcp://PI_IP:9002
```
2) If debug works but GUI does not: likely PyAV/FFmpeg setup on the client.
