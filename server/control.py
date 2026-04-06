from __future__ import annotations
import json
import socket
import threading
from typing import Callable, Dict, Any, Optional

class ControlServer:
    def __init__(
        self,
        host: str,
        port: int,
        on_command: Callable[[Dict[str, Any]], Dict[str, Any]],
        logger: Callable[[str], None],
    ) -> None:
        self.host = host
        self.port = port
        self.on_command = on_command
        self.log = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="control-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(5)
            srv.settimeout(1.0)
            self.log(f"[control] listening on {self.host}:{self.port}")

            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()

    def _handle_client(self, conn: socket.socket, addr) -> None:
        self.log(f"[control] client connected: {addr}")
        with conn:
            conn.settimeout(2.0)
            buf = b""
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                        resp = self.on_command(req)
                        out = (json.dumps({"ok": True, "resp": resp}) + "\n").encode("utf-8")
                    except Exception as e:
                        out = (json.dumps({"ok": False, "error": str(e)}) + "\n").encode("utf-8")
                    try:
                        conn.sendall(out)
                    except OSError:
                        break
        self.log(f"[control] client disconnected: {addr}")
