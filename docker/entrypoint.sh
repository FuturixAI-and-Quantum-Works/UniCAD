#!/bin/sh
# Starts a private X server for LibreCAD, then runs unicad-mcp with the given arguments or the given command.
set -eu

display="${DISPLAY:-:99}"
number="${display#:}"
number="${number%%.*}"

Xvfb "$display" -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!

tries=0
while [ ! -S "/tmp/.X11-unix/X${number}" ]; do
  tries=$((tries + 1))
  if [ "$tries" -gt 100 ] || ! kill -0 "$xvfb_pid" 2>/dev/null; then
    echo "Xvfb did not start on $display:" >&2
    cat /tmp/xvfb.log >&2
    exit 1
  fi
  sleep 0.1
done

case "${1:-}" in
  "" | -*) set -- unicad-mcp "$@" ;;
esac

trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT
"$@" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true' INT TERM
status=0
wait "$child" || status=$?
if kill -0 "$child" 2>/dev/null; then
  wait "$child" || status=$?
fi
exit "$status"
