from __future__ import annotations
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Deque
from collections import deque

from protocol import pack_header, FLAG_KEYFRAME
from h264_parser import nal_type

@dataclass
class ClientCounts:
    framed: int = 0
    debug: int = 0

class VideoServer:
    """Two TCP outputs:
    - framed: header+payload per NAL
    - debug: raw H.264 bytestream (NALs concatenated)

    SPS/PPS patch:
    - Cache last SPS (7) and PPS (8)
    - On new connection: wait for next IDR (5), then send SPS+PPS before IDR.
    """
    def __init__(
        self,
        host: str,
        framed_port: int,
        debug_port: int,
        logger: Callable[[str], None],
        max_queue: int = 8000,
    ) -> None:
        self.host = host
        self.framed_port = framed_port
        self.debug_port = debug_port
        self.log = logger

        self._framed_conn: Optional[socket.socket] = None
        self._debug_conn: Optional[socket.socket] = None
        self._lock = threading.Lock()

        self._framed_need_idr = False
        self._debug_need_idr = False

        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None

        self._q: Deque[bytes] = deque(maxlen=max_queue)
        self._stop = threading.Event()
        self._counts = ClientCounts()

    def start(self) -> None:
        threading.Thread(target=self._accept_loop, args=("framed", self.framed_port), daemon=True).start()
        threading.Thread(target=self._accept_loop, args=("debug", self.debug_port), daemon=True).start()
        threading.Thread(target=self._send_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for c in (self._framed_conn, self._debug_conn):
                if c is not None:
                    try: c.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    try: c.close()
                    except OSError: pass
            self._framed_conn = None
            self._debug_conn = None
            self._counts = ClientCounts()
            self._framed_need_idr = False
            self._debug_need_idr = False

    def client_counts(self) -> ClientCounts:
        return ClientCounts(self._counts.framed, self._counts.debug)

    def push_nal(self, nal: bytes) -> None:
        # Update SPS/PPS cache
        t = nal_type(nal)
        if t == 7:
            self._sps = nal
        elif t == 8:
            self._pps = nal
        self._q.append(nal)

    def _accept_loop(self, kind: str, port: int) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, port))
            srv.listen(1)
            srv.settimeout(1.0)
            self.log(f"[video:{kind}] listening on {self.host}:{port}")

            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                try:
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
                conn.settimeout(2.0)

                with self._lock:
                    if kind == "framed":
                        if self._framed_conn is not None:
                            try: self._framed_conn.close()
                            except OSError: pass
                        self._framed_conn = conn
                        self._counts.framed = 1
                        self._framed_need_idr = True
                    else:
                        if self._debug_conn is not None:
                            try: self._debug_conn.close()
                            except OSError: pass
                        self._debug_conn = conn
                        self._counts.debug = 1
                        self._debug_need_idr = True

                self.log(f"[video:{kind}] client connected: {addr} (will start on next IDR)")

    def _send_framed(self, sock: socket.socket, nal: bytes, pts_us: int) -> None:
        t = nal_type(nal)
        flags = FLAG_KEYFRAME if t == 5 else 0
        payload = pack_header(flags=flags, pts_us=pts_us, payload_len=len(nal)) + nal
        sock.sendall(payload)

    def _send_loop(self) -> None:
        pts0 = time.monotonic()
        while not self._stop.is_set():
            if not self._q:
                time.sleep(0.001)
                continue

            nal = self._q.popleft()
            pts_us = int((time.monotonic() - pts0) * 1_000_000)
            t = nal_type(nal)

            with self._lock:
                f = self._framed_conn
                d = self._debug_conn
                framed_need_idr = self._framed_need_idr
                debug_need_idr = self._debug_need_idr
                sps = self._sps
                pps = self._pps

            # If client is new, skip until IDR
            if framed_need_idr and t != 5:
                pass
            elif f is not None:
                try:
                    if framed_need_idr and t == 5:
                        # inject SPS/PPS before IDR
                        if sps: self._send_framed(f, sps, pts_us)
                        if pps: self._send_framed(f, pps, pts_us)
                        with self._lock:
                            self._framed_need_idr = False
                    self._send_framed(f, nal, pts_us)
                except OSError:
                    with self._lock:
                        try: f.close()
                        except OSError: pass
                        if self._framed_conn is f:
                            self._framed_conn = None
                            self._counts.framed = 0
                            self._framed_need_idr = False
                    self.log("[video:framed] client disconnected (send error)")

            if debug_need_idr and t != 5:
                continue

            if d is not None:
                try:
                    if debug_need_idr and t == 5:
                        # inject SPS/PPS before IDR
                        if sps: d.sendall(sps)
                        if pps: d.sendall(pps)
                        with self._lock:
                            self._debug_need_idr = False
                    d.sendall(nal)
                except OSError:
                    with self._lock:
                        try: d.close()
                        except OSError: pass
                        if self._debug_conn is d:
                            self._debug_conn = None
                            self._counts.debug = 0
                            self._debug_need_idr = False
                    self.log("[video:debug] client disconnected (send error)")
