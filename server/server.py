#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from dataclasses import asdict

from video_server import VideoServer
from control import ControlServer
from capture_picamera2 import CaptureConfig, Picamera2Capture

class App:
    def __init__(self, host: str, video_port: int, control_port: int, debug_port: int) -> None:
        self.host = host
        self.video_port = video_port
        self.control_port = control_port
        self.debug_port = debug_port

        self.cfg = CaptureConfig()
        self.video = VideoServer(host=self.host, framed_port=self.video_port, debug_port=self.debug_port, logger=self.log)
        self.control = ControlServer(host=self.host, port=self.control_port, on_command=self.on_command, logger=self.log)
        self.capture = Picamera2Capture(cfg=self.cfg, on_nal=self.video.push_nal, logger=self.log)

    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def start(self) -> None:
        self.video.start()
        self.control.start()
        self.capture.start()
        self.log("[app] started")

    def stop(self) -> None:
        try: self.capture.stop()
        except Exception: pass
        try: self.video.stop()
        except Exception: pass
        try: self.control.stop()
        except Exception: pass
        self.log("[app] stopped")

    def on_command(self, req: dict) -> dict:
        cmd = (req.get("cmd") or "").lower()

        if cmd == "get_status":
            counts = self.video.client_counts()
            return {
                "running": True,
                "capture": asdict(self.cfg),
                "clients": {"framed": counts.framed, "debug": counts.debug},
            }

        if cmd == "set_params":
            changed = False
            for k in ("width", "height", "fps", "bitrate", "keyframe_period", "rotation"):
                if k in req:
                    setattr(self.cfg, k, int(req[k]))
                    changed = True
            if changed:
                self.capture.stop()
                self.capture = Picamera2Capture(cfg=self.cfg, on_nal=self.video.push_nal, logger=self.log)
                self.capture.start()
            return {"applied": asdict(self.cfg)}

        if cmd == "restart":
            self.capture.stop()
            self.capture = Picamera2Capture(cfg=self.cfg, on_nal=self.video.push_nal, logger=self.log)
            self.capture.start()
            return {"ok": True}

        raise ValueError(f"unknown cmd: {cmd}")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--video-port", type=int, default=9000)
    ap.add_argument("--control-port", type=int, default=9001)
    ap.add_argument("--debug-port", type=int, default=9002)
    args = ap.parse_args()

    app = App(args.host, args.video_port, args.control_port, args.debug_port)
    try:
        app.start()
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
