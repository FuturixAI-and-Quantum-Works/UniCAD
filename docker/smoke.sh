#!/bin/sh
# Smoke test for the UniCAD image: render a fixture SLD to DXF/PDF/DWG/PNG inside the container, then check the HTTP server.
# Usage: docker/smoke.sh [image]   (default unicad-mcp:local, built from the repository root when missing)
set -eu

image="${1:-unicad-mcp:local}"
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
name="unicad-smoke-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$out"' EXIT
chmod 777 "$out"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build -t "$image" "$root"
fi

docker run --rm \
  -v "$root/fixtures:/fixtures:ro" \
  -v "$root/docker/smoke_render.py:/smoke/smoke_render.py:ro" \
  -v "$out:/out" \
  "$image" python /smoke/smoke_render.py

for f in rooftop_500kwp-sld.dxf rooftop_500kwp-sld.pdf rooftop_500kwp-sld.dwg rooftop_500kwp-sld.png; do
  test -s "$out/$f" || { echo "missing or empty: $f" >&2; exit 1; }
done
head -c 4 "$out/rooftop_500kwp-sld.pdf" | grep -q '%PDF' || { echo "not a PDF" >&2; exit 1; }
head -c 6 "$out/rooftop_500kwp-sld.dwg" | grep -q '^AC10' || { echo "not a DWG" >&2; exit 1; }

docker run -d --name "$name" -e UNICAD_TOKEN=smoke-token -p 127.0.0.1::8765 "$image" >/dev/null
port="$(docker port "$name" 8765/tcp | head -n1 | sed 's/.*://')"
tries=0
until docker run --rm --network container:"$name" --entrypoint python "$image" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)" >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -le 30 ] || { docker logs "$name" >&2; echo "server did not become healthy" >&2; exit 1; }
  sleep 1
done
code="$(docker run --rm --network container:"$name" --entrypoint python "$image" -c "
import urllib.error, urllib.request
req = urllib.request.Request('http://127.0.0.1:8765/mcp', data=b'{}', method='POST', headers={'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(req, timeout=5)
except urllib.error.HTTPError as e:
    print(e.code)
")"
[ "$code" = "401" ] || { echo "expected 401 without a token, got '$code'" >&2; exit 1; }
echo "smoke ok: SLD rendered to DXF, PDF, DWG, PNG; HTTP server healthy on port $port and rejects a missing token"
