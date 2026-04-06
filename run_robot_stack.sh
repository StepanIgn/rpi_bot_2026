#!/usr/bin/env bash
set -euo pipefail

# Adjust paths if you move the project.
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Video (TCP Camera) ---
VIDEO_DIR="$BASE_DIR/server"
VIDEO_HOST="0.0.0.0"
VIDEO_PORT="9000"
# Use 9009 so it doesn't collide with robot gimbal (9002).
DEBUG_PORT="9009"

# --- Robot control ---
ROBOT_SERVER="$BASE_DIR/robot_control_server.py"

echo "[stack] starting video-only server..."
cd "$VIDEO_DIR"
python3 tcp_video_stream.py --host "$VIDEO_HOST" --video-port "$VIDEO_PORT" --debug-port "$DEBUG_PORT" &
VIDEO_PID=$!

echo "[stack] starting robot control server..."
cd "$BASE_DIR"
python3 "$ROBOT_SERVER" &
ROBOT_PID=$!

cleanup() {
  echo "[stack] stopping..."
  kill -TERM "$ROBOT_PID" "$VIDEO_PID" 2>/dev/null || true
  wait "$ROBOT_PID" 2>/dev/null || true
  wait "$VIDEO_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for any process to exit; if one dies, stop the other so systemd can restart the stack.
while true; do
  if ! kill -0 "$VIDEO_PID" 2>/dev/null; then
    echo "[stack] video server exited"
    exit 1
  fi
  if ! kill -0 "$ROBOT_PID" 2>/dev/null; then
    echo "[stack] robot control server exited"
    exit 1
  fi
  sleep 1
done
