from __future__ import annotations
import socket

def connect_tcp(host: str, port: int, timeout_s: float = 5.0) -> socket.socket:
    s = socket.create_connection((host, port), timeout=timeout_s)
    s.settimeout(2.0)
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    return s

def recv_exact(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("socket closed")
        data.extend(chunk)
    return bytes(data)
