"""Country borders (Natural Earth, public domain) as Mercator polylines, drawn over the zoom-out."""
import json
import cv2
import numpy as np
from .config import NE_DIR
from .imagery import R_EARTH, MAX_LAT


def _lines(geom):
    t, c = geom.get("type"), geom.get("coordinates", [])
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return c
    if t == "Polygon":
        return c
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    return []


class Borders:
    def __init__(self, files=("ne_50m_admin_0_boundary_lines_land.geojson",)):
        self.lines = []
        for name in files:
            p = NE_DIR / name
            if not p.exists():
                print(f"  borders: {name} missing - zoom-out without borders")
                continue
            for f in json.loads(p.read_text(encoding="utf-8")).get("features", []):
                for line in _lines(f.get("geometry") or {}):
                    a = np.asarray(line, np.float64)
                    if len(a) < 2:
                        continue
                    lat = np.clip(a[:, 1], -MAX_LAT, MAX_LAT)
                    x = R_EARTH * np.radians(a[:, 0])
                    y = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
                    self.lines.append(np.stack([x, y], 1))
        if self.lines:
            self.boxes = np.array([[l[:, 0].min(), l[:, 1].min(), l[:, 0].max(), l[:, 1].max()] for l in self.lines])

    def visible(self, x0, y0, x1, y1):
        """Polylines that touch the Mercator box."""
        if not self.lines:
            return []
        b = self.boxes
        m = (b[:, 2] >= x0) & (b[:, 0] <= x1) & (b[:, 3] >= y0) & (b[:, 1] <= y1)
        return [self.lines[i] for i in np.flatnonzero(m)]


# ------------------------------------------------------------------ backup map for the zoom-out
LAND = (58, 78, 56)


def _merc_xy(a):
    lat = np.clip(a[:, 1], -MAX_LAT, MAX_LAT)
    return np.stack([R_EARTH * np.radians(a[:, 0]), R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))], 1)


def land_layers(lon, lat, reveal_km, name="ne_50m_admin_0_countries.geojson"):
    """Drawn land/ocean map (Natural Earth) - only used if NASA Blue Marble cannot be downloaded."""
    from .imagery import Layer, square_bounds, OCEAN
    p = NE_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"{name} missing (run scripts/fetch_assets.sh)")
    rings = []
    for f in json.loads(p.read_text(encoding="utf-8")).get("features", []):
        g = f.get("geometry") or {}
        polys = [g.get("coordinates", [])] if g.get("type") == "Polygon" else g.get("coordinates", []) \
            if g.get("type") == "MultiPolygon" else []
        for poly in polys:
            if poly:
                rings.append(_merc_xy(np.asarray(poly[0], np.float64)))
    out = []
    for side in (max(1900.0, reveal_km * 0.6), reveal_km * 2.0):
        size = 2048
        x0, y0, x1, y1 = square_bounds(lon, lat, side)
        img = np.zeros((size, size, 3), np.uint8); img[:] = OCEAN
        sx = size / (x1 - x0)
        pts = [np.round(np.stack([(r[:, 0] - x0) * sx, (y1 - r[:, 1]) * sx], 1)).astype(np.int32) for r in rings]
        mask = np.zeros((size, size), np.uint8)
        cv2.fillPoly(mask, pts, 255, lineType=cv2.LINE_AA)
        rng = np.random.default_rng(3)
        tex = cv2.GaussianBlur(rng.normal(0, 1, (size // 8, size // 8)).astype(np.float32), (0, 0), 2)
        tex = cv2.resize(tex, (size, size))[..., None] * 9
        land = np.clip(np.array(LAND, np.float32) + tex, 0, 255)
        m = (mask.astype(np.float32) / 255)[..., None]
        img = (img * (1 - m) + land * m).astype(np.uint8)
        out.append(Layer(img, (x0, y0, x1, y1), f"natural earth {side:.0f} km"))
    return out
