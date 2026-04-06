#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from video_server import VideoServer
from capture_picamera2 import CaptureConfig, Picamera2Capture


class App:
    def __init__(self, host: str, video_port: int, debug_port: int) -> None:
        self.host = host
        self.video_port = video_port
        self.debug_port = debug_port

        self.cfg = CaptureConfig()
        # If debug_port==0, disable debug stream.
        dbg = debug_port if debug_port and debug_port > 0 else None
        self.video = VideoServer(host=self.host, framed_port=self.video_port, debug_port=dbg, logger=self.log)
        self.capture = Picamera2Capture(cfg=self.cfg, on_nal=self.video.push_nal, logger=self.log)

    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def start(self) -> None:
        self.video.start()
        self.capture.start()
        self.log("[video-only] started")

    def stop(self) -> None:
        try:
            self.capture.stop()
        except Exception:
            pass
        try:
            self.video.stop()
        except Exception:
            pass
        self.log("[video-only] stopped")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--video-port", type=int, default=9000)
    ap.add_argument("--debug-port", type=int, default=9002,
                    help="Set to 0 to disable debug port")
    args = ap.parse_args()

    app = App(args.host, args.video_port, args.debug_port)
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
