#!/usr/bin/env bash
# Downloads the Natural Earth map data (public domain): country borders for the zoom-out and the land shapes for
# the backup map. Safe to re-run. The satellite images themselves are fetched by the bot (no download here).
set -euo pipefail
cd "$(dirname "$0")/.."
NE=data/naturalearth; mkdir -p "$NE"
# pinned to a fixed commit so the files can never change underneath us (same as the GeoGuess bot)
BASE=https://raw.githubusercontent.com/nvkelso/natural-earth-vector/ca96624a56bd078437bca8184e78163e5039ad19/geojson
for f in ne_50m_admin_0_boundary_lines_land ne_50m_admin_0_countries; do
  [ -s "$NE/$f.geojson" ] || curl -fsSL --retry 3 -o "$NE/$f.geojson" "$BASE/$f.geojson"
done
# sanity check: an error page instead of a data file would be tiny
for f in "$NE"/*.geojson; do
  [ "$(stat -c%s "$f")" -gt 10000 ] || { echo "ERROR: $f looks broken (too small)"; rm -f "$f"; exit 1; }
done
echo "SHA-256 of downloaded files:"
sha256sum "$NE"/*.geojson
