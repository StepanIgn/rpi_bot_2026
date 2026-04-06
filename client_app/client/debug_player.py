from __future__ import annotations
import shutil
import subprocess
from typing import Optional

class DebugPlayer:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def start_ffplay(self, host: str, port: int) -> None:
        self.stop()
        if shutil.which("ffplay") is None:
            raise RuntimeError("ffplay not found in PATH (install FFmpeg)")
        url = f"tcp://{host}:{port}"
        args = ["ffplay", "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop", "-f", "h264", url]
        self._proc = subprocess.Popen(args)

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
