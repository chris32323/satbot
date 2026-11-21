"""Satellite imagery for the quiz - all free, no account needed.

Detail (quiz view, up to ~120 km): Sentinel-2 L2A true colour (10 m) from the AWS Open Data archive, found via the
Earth Search STAC API. Tiles of the SAME day are mosaicked (no seams), clouds are checked with the scene
classification (SCL). Licence: Copernicus Sentinel data, free/full/open - credit "Contains modified Copernicus
Sentinel data <year>".
Wide (continent): NASA Blue Marble Next Generation (cloud-free, 500 m) via NASA GIBS WMS.

Everything is resampled into Web-Mercator "layers" (RGB image + Mercator bounds); the renderer flies a camera over them.
"""
import hashlib, io, math, os, time
from datetime import datetime, timezone
import numpy as np
import cv2
import requests
from PIL import Image

from .config import (STAC_URL, S2_COLLECTIONS, S2_MAX_CLOUD, GIBS_WMS, GIBS_LAYERS, CACHE, PC_STAC_URL,
                     PC_TOKEN_URL)
from .safety import redact

R_EARTH = 6378137.0
H_RATIO = 1920 / 1080                        # portrait video: height / width
MAX_LAT = 85.0
OCEAN = (14, 32, 62)                         # fill colour outside Blue Marble requests (deep ocean blue)


class ImageryError(RuntimeError):
    """No usable imagery for this place (clouds, gaps) - the place is paused for a while, try another one."""


class ImageryTrouble(RuntimeError):
    """Service, network or software problem - NOT the place's fault, so it is not paused."""


# ------------------------------------------------------------------ projection helpers
def merc(lon, lat):
    lat = max(min(lat, MAX_LAT), -MAX_LAT)
    return R_EARTH * math.radians(lon), R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def inv_merc(x, y):
    return math.degrees(x / R_EARTH), math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)


def ground_to_merc(km, lat):
    """Ground distance (km) at latitude -> Web-Mercator metres."""
    return km * 1000.0 / max(math.cos(math.radians(lat)), 0.05)


class Layer:
    """RGB uint8 image covering the Mercator box (x0, y0) - (x1, y1); row 0 is the north edge."""

    def __init__(self, img, bounds, name):
        self.img = np.ascontiguousarray(img[..., :3]).astype(np.uint8)
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.name = name
        self.h, self.w = self.img.shape[:2]
        self.px = (self.x1 - self.x0) / self.w          # Mercator metres per pixel

    def contains(self, x0, y0, x1, y1, margin=0.0):
        m = margin * self.px
        return x0 >= self.x0 + m and x1 <= self.x1 - m and y0 >= self.y0 + m and y1 <= self.y1 - m


def square_bounds(lon, lat, side_km):
    cx, cy = merc(lon, lat)
    half = ground_to_merc(side_km, lat) / 2
    return cx - half, cy - half, cx + half, cy + half


# ------------------------------------------------------------------ small HTTP helpers
def _get(url, **kw):
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: {r.text[:160]}"
            if r.status_code in (400, 401, 403, 404):
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3 * (attempt + 1))
    raise ImageryTrouble(redact(f"download failed ({last})"))


def _cache_file(*parts, ext="npy"):
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / (hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:24] + "." + ext)


# ------------------------------------------------------------------ Blue Marble (NASA GIBS)
def _gibs(bbox, w, h):
    """One Blue Marble picture for a lon/lat box (EPSG:4326, -180..180), cached on disk."""
    cache = _cache_file("bm", *[round(v, 4) for v in bbox], w, h, ext="jpg")
    if cache.exists():
        return np.array(Image.open(cache).convert("RGB"))
    err = None
    for layer in GIBS_LAYERS:
        try:
            r = _get(GIBS_WMS, params=dict(SERVICE="WMS", VERSION="1.1.1", REQUEST="GetMap", LAYERS=layer,
                                          STYLES="", SRS="EPSG:4326", FORMAT="image/jpeg",
                                          BBOX="{:.6f},{:.6f},{:.6f},{:.6f}".format(*bbox), WIDTH=w, HEIGHT=h))
            if not r.headers.get("content-type", "").startswith("image"):
                raise ImageryTrouble(f"GIBS returned {r.headers.get('content-type')}: {r.text[:120]}")
            img = np.array(Image.open(io.BytesIO(r.content)).convert("RGB"))
            cache.write_bytes(r.content)
            return img
        except Exception as e:
            err = e
    raise ImageryTrouble(f"Blue Marble not available ({err})")


def bluemarble(lon, lat, side_km, size=2048):
    """Cloud-free Blue Marble around (lon, lat), side_km wide, as a Mercator layer (fetched in EPSG:4326 and
    reprojected here). Views across the date line (e.g. New Zealand) are stitched from two requests."""
    x0, y0, x1, y1 = square_bounds(lon, lat, side_km)
    lim = merc(0, MAX_LAT)[1]
    y0c, y1c = max(y0, -lim), min(y1, lim)
    lon0, lat0 = inv_merc(x0, y0c); lon1, lat1 = inv_merc(x1, y1c)       # lon0/lon1 may pass +-180
    lon1 = min(lon1, lon0 + 360.0)
    rlat0, rlat1 = max(lat0, -MAX_LAT), min(lat1, MAX_LAT)
    total_w = min(4096, max(512, int(size * 1.2)))
    rh = min(4096, max(512, int(size * 1.2 * (y1c - y0c) / max(y1 - y0, 1e-9))))   # enough rows for Mercator
    parts, a = [], lon0
    while a < lon1 - 1e-9:                                                # split at the date line
        wa = (a + 180.0) % 360.0 - 180.0
        b = min(lon1, a + (180.0 - wa))
        w = max(16, int(round(total_w * (b - a) / (lon1 - lon0))))
        parts.append(_gibs((wa, rlat0, wa + (b - a), rlat1), w, rh))
        a = b
    src = parts[0] if len(parts) == 1 else np.hstack([p[:min(q.shape[0] for q in parts)] for p in parts])
    # reproject equirectangular -> Mercator grid
    xs = x0 + (np.arange(size) + 0.5) * (x1 - x0) / size
    ys = y1 - (np.arange(size) + 0.5) * (y1 - y0) / size
    lons = np.degrees(xs / R_EARTH)
    lats = np.degrees(2 * np.arctan(np.exp(ys / R_EARTH)) - np.pi / 2)
    mx = ((lons - lon0) / (lon1 - lon0) * src.shape[1] - 0.5).astype(np.float32)
    my = ((rlat1 - lats) / (rlat1 - rlat0) * src.shape[0] - 0.5).astype(np.float32)
    mapx = np.broadcast_to(mx[None, :], (size, size)).copy()
    mapy = np.broadcast_to(my[:, None], (size, size)).copy()
    img = cv2.remap(src, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=OCEAN)
    return Layer(img, (x0, y0, x1, y1), f"bluemarble {side_km:.0f} km")


# ------------------------------------------------------------------ Sentinel-2 (Earth Search STAC + COGs)
def _https(asset):
    """Asset href as https URL (some collections list s3:// with an https alternate)."""
    href = asset.get("href", "")
    if href.startswith("http"):
        return href
    alt = (asset.get("alternate") or {}).get("https", {}).get("href")
    if alt:
        return alt
    if href.startswith("s3://"):
        bucket, key = href[5:].split("/", 1)
        return f"https://{bucket}.s3.us-west-2.amazonaws.com/{key}"
    return href


def _point_in_poly(x, y, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def _covers(item, lon, lat):
    g = item.get("geometry") or {}
    polys = g.get("coordinates", [])
    if g.get("type") == "Polygon":
        polys = [polys]
    return any(_point_in_poly(lon, lat, p[0]) for p in polys if p)


SOURCES = [   # tried in this order; the second one is only used if the first is down or finds nothing
    dict(name="earth-search", url=STAC_URL, collections=S2_COLLECTIONS, visual="visual", scl="scl", sign=None),
    dict(name="planetary-computer", url=PC_STAC_URL, collections=["sentinel-2-l2a"], visual="visual", scl="SCL",
         sign="pc"),
]
_pc_tokens = {}


def _pc_sign(href, collection):
    """Microsoft Planetary Computer: free anonymous SAS token, appended to the blob URL."""
    tok = _pc_tokens.get(collection)
    if not tok or tok[1] < time.time() + 600:
        r = _get(f"{PC_TOKEN_URL}/{collection}")
        j = r.json()
        exp = j.get("msft:expiry", "")
        try:
            t_exp = datetime.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            t_exp = time.time() + 1800
        tok = (j["token"], t_exp); _pc_tokens[collection] = tok
    return href + ("&" if "?" in href else "?") + tok[0]


def asset_href(item, kind):
    """https URL of the 'visual' (true colour) or 'scl' (scene classification) asset of a STAC item, or None."""
    src = item.get("_src") or SOURCES[0]
    asset = (item.get("assets") or {}).get(src[kind])
    if not asset:
        return None
    href = _https(asset)
    if src.get("sign") == "pc":
        href = _pc_sign(href, item.get("_collection", "sentinel-2-l2a"))
    return href


def season_windows(lat, today=None):
    """Search windows: the newest scenes, plus the last two summers (bright, no snow) away from the tropics."""
    today = today or datetime.now(timezone.utc).date()
    wins = ["2019-01-01T00:00:00Z/.."]
    if abs(lat) >= 28:
        for y in range(today.year, today.year - 3, -1):
            if lat > 0:
                s0, s1 = f"{y}-05-01", f"{y}-09-30"
            else:
                s0, s1 = f"{y - 1}-11-01", f"{y}-03-31"
            if s1 < today.isoformat():
                wins.append(f"{s0}T00:00:00Z/{s1}T23:59:59Z")
            if len(wins) == 3:
                break
    return wins


def _scene_key(f):
    """Same tile + same day = same scene (the two Earth Search collections hold the same acquisitions)."""
    p = f.get("properties", {})
    tile = p.get("grid:code") or p.get("s2:mgrs_tile")
    if not tile:
        return f.get("id")
    tile = str(tile).replace("MGRS-", "")
    return f"{tile}|{(p.get('datetime') or '')[:10]}|{str(p.get('platform', '')).lower()}"


def stac_search(lon0, lat0, lon1, lat1, limit=100, windows=None, log=print, max_cloud=None):
    """Sentinel-2 scenes (< S2_MAX_CLOUD % clouds) touching the box, newest first, from the first source that works."""
    windows = windows or ["2019-01-01T00:00:00Z/.."]
    max_cloud = S2_MAX_CLOUD if max_cloud is None else max_cloud
    errors = []
    for src in SOURCES:
        feats, seen, err = [], set(), None
        for col in src["collections"]:
            for win in windows:
                body = {"collections": [col], "bbox": [lon0, lat0, lon1, lat1], "limit": limit, "datetime": win,
                        "query": {"eo:cloud_cover": {"lt": max_cloud}},
                        "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
                for attempt in range(3):
                    try:
                        r = requests.post(src["url"], json=body, timeout=90)
                        if r.status_code == 400 and "sortby" in body:          # server without sort extension
                            body.pop("sortby"); continue
                        if r.status_code == 200:
                            for f in r.json().get("features", []):
                                k = _scene_key(f)
                                if src["visual"] in (f.get("assets") or {}) and k not in seen:
                                    seen.add(k); f["_collection"] = col; f["_src"] = src
                                    feats.append(f)
                            err = None
                            break
                        err = f"HTTP {r.status_code}: {r.text[:160]}"
                    except (requests.RequestException, ValueError) as e:
                        err = str(e)
                    time.sleep(3 * (attempt + 1))
            if len(feats) >= 40:                     # enough scenes - the older collection is not needed
                break
        if feats:
            if src is not SOURCES[0]:
                log(f"  using {src['name']} (backup source)")
            return feats
        if err:
            errors.append(f"{src['name']}: {err}")
    if errors:
        raise ImageryTrouble(redact("Sentinel-2 search failed (" + "; ".join(errors) + ")"))
    return []


def _season_penalty(date, lat):
    m = date.month
    if abs(lat) < 28:
        return 0
    winter = (11, 12, 1, 2) if lat > 0 else (5, 6, 7, 8)
    return 25 if m in winter else 0


def _groups(feats, lon, lat, lon0, lat0, lon1, lat1):
    """Group scenes by acquisition day; keep days whose scenes together cover the given area."""
    by_day = {}
    for f in feats:
        day = (f.get("properties", {}).get("datetime") or "")[:10]
        if day:
            by_day.setdefault(day, []).append(f)
    pts = [(lon0 + (lon1 - lon0) * i / 6, lat0 + (lat1 - lat0) * j / 6) for i in range(7) for j in range(7)]
    out = []
    for day, items in by_day.items():
        if not all(any(_covers(f, px, py) for f in items) for px, py in pts):
            continue
        d = datetime.strptime(day, "%Y-%m-%d")
        cloud = float(np.mean([f["properties"].get("eo:cloud_cover", 50) for f in items]))
        age_years = max(0.0, (datetime.now(timezone.utc).replace(tzinfo=None) - d).days / 365)
        score = cloud + _season_penalty(d, lat) + 2 * age_years
        items.sort(key=lambda f: (not _covers(f, lon, lat), f["properties"].get("eo:cloud_cover", 50)))
        out.append((score, day, items))
    out.sort(key=lambda g: g[0])
    return out


def _warp(href, bounds, size, lat, nearest=False, bands=3):
    """Read a COG straight into our Mercator grid (only the needed bytes, using the right overview level)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT
    x0, y0, x1, y1 = bounds
    target = (x1 - x0) / size * math.cos(math.radians(lat))           # ground metres per output pixel
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIRANGE="YES",
               GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", VSI_CACHE="TRUE", GDAL_HTTP_MAX_RETRY="4",
               GDAL_HTTP_RETRY_DELAY="2", GDAL_HTTP_TIMEOUT="90", AWS_NO_SIGN_REQUEST="YES")
    # rasterio wheels look for the CA bundle at the Red Hat path; on Ubuntu runners point them to the system one
    if not os.getenv("CURL_CA_BUNDLE") and os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
        env["CURL_CA_BUNDLE"] = "/etc/ssl/certs/ca-certificates.crt"
    with rasterio.Env(**env):
        with rasterio.open(href) as src:
            native = abs(src.transform.a)
            factors = src.overviews(1) or []
        level = None
        for i, f in enumerate(factors):
            if native * f <= target * 1.25:
                level = i
        opts = {} if level is None else {"overview_level": level}
        with rasterio.open(href, **opts) as src:
            with WarpedVRT(src, crs="EPSG:3857", transform=from_bounds(x0, y0, x1, y1, size, size),
                           width=size, height=size, src_nodata=0, nodata=0,
                           resampling=Resampling.nearest if nearest else Resampling.bilinear) as vrt:
                arr = vrt.read(indexes=list(range(1, bands + 1)))
    return np.moveaxis(arr, 0, -1)


def _mosaic(items, kind, bounds, size, lat, nearest=False, bands=3):
    out = None
    for f in items:
        a = _warp(asset_href(f, kind), bounds, size, lat, nearest=nearest, bands=bands)
        if out is None:
            out = a
        else:
            hole = np.all(out == 0, axis=-1)
            out[hole] = a[hole]
        if not np.any(np.all(out == 0, axis=-1)):
            break
    return out


def enhance(img, ref=None):
    """Punchy but natural colours: shared percentile stretch, slight gamma and saturation."""
    ref = img if ref is None else ref
    valid = ref[np.any(ref > 0, axis=-1)]
    if len(valid) < 100:
        return img
    lo = min(float(np.percentile(valid, 0.8)), 28.0)              # same stretch for R, G, B = natural colours
    hi = float(np.percentile(valid, 99.6))
    hi = min(max(hi, lo + 120.0), 255.0)                          # mostly ocean: do not blow up the noise
    x = np.clip((img.astype(np.float32) - lo) / (hi - lo), 0, 1) ** 0.92
    hsv = cv2.cvtColor((x * 255).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.15, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def _fill_holes(img, wide, log, day):
    """No-data at the outer edge of the big layer: fill with Blue Marble, or (if that fails) smooth colours."""
    holes = np.all(img == 0, axis=-1)
    if not holes.any():
        return
    log(f"  {day}: filling {holes.mean() * 100:.1f} % at the edges")
    try:
        fill = cv2.resize(wide(), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    except Exception:
        small = cv2.resize(img, (450, 450), interpolation=cv2.INTER_AREA)
        m = cv2.resize(holes.astype(np.uint8) * 255, (450, 450), interpolation=cv2.INTER_NEAREST)
        fill = cv2.resize(cv2.inpaint(small, m, 9, cv2.INPAINT_TELEA), (img.shape[1], img.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
    img[holes] = fill[holes]


CLOUD_CLASSES = (3, 8, 9, 10)               # SCL: cloud shadow, cloud medium/high, thin cirrus


def sentinel_layers(lon, lat, quiz_km, log=print, max_days=4, check=None, bright=False):
    """Two Sentinel-2 layers around the place: 'hi' (sharp, for the fly-in) and 'quiz' (3x the quiz width,
    for drifting and the zoom-out). check(preview) -> False rejects a day (AI image check).
    bright=True for salt flats / white dunes, which the cloud mask often mistakes for clouds.
    Returns (layers, info) or raises ImageryError."""
    try:
        import rasterio  # noqa: F401  (installed on the runner via requirements.txt)
    except ImportError:
        raise ImageryTrouble("rasterio is not installed (pip install -r requirements.txt)")
    q_bounds = square_bounds(lon, lat, quiz_km * 3.0)
    h_bounds = square_bounds(lon, lat, quiz_km * 0.95)
    lon0, lat0 = inv_merc(q_bounds[0], q_bounds[1]); lon1, lat1 = inv_merc(q_bounds[2], q_bounds[3])
    feats = stac_search(lon0, lat0, lon1, lat1, windows=season_windows(lat), log=log,
                        max_cloud=max(S2_MAX_CLOUD, 50) if bright else None)
    max_scl_cloud = 0.25 if bright else 0.04
    groups = _groups(feats, lon, lat, lon0, lat0, lon1, lat1)
    if not groups:                       # no day covers the whole 3x area: accept days covering the core
        c_bounds = square_bounds(lon, lat, quiz_km * 1.9)
        clon0, clat0 = inv_merc(c_bounds[0], c_bounds[1]); clon1, clat1 = inv_merc(c_bounds[2], c_bounds[3])
        groups = _groups(feats, lon, lat, clon0, clat0, clon1, clat1)
    log(f"  sentinel-2: {len(feats)} scenes, {len(groups)} usable days")
    if not groups:
        raise ImageryError("no clear Sentinel-2 day covers this area")
    v_bounds = square_bounds(lon, lat, quiz_km * 1.8)                 # what the quiz view really shows
    errors = []
    for score, day, items in groups[:max_days]:
        try:
            size = 1200
            has_scl = all(f["_src"]["scl"] in (f.get("assets") or {}) for f in items)
            scl = _mosaic(items, "scl", v_bounds, size // 2, lat, nearest=True, bands=1)[..., 0] if has_scl else None
            if scl is not None:
                valid = scl > 0
                nodata = 1 - valid.mean()
                cloud = np.isin(scl, CLOUD_CLASSES)[valid].mean() if valid.any() else 1
                log(f"  {day}: clouds {cloud * 100:.1f} %, gaps {nodata * 100:.1f} % ({len(items)} tiles)")
                if cloud > max_scl_cloud or nodata > 0.005:
                    continue
            quiz = _mosaic(items, "visual", q_bounds, 3600, lat)
            if np.all(quiz == 0, axis=-1)[900:2700, 900:2700].mean() > 0.005:
                log(f"  {day}: gaps in the image, next day"); continue
            hi = _mosaic(items, "visual", h_bounds, 2048, lat)
            ref = quiz[1200:2400, 1200:2400]
            _fill_holes(quiz, lambda: bluemarble(lon, lat, quiz_km * 3.0, size=900).img, log, day)
            hole = np.all(hi == 0, axis=-1)
            if hole.any():                                            # tiny gaps in the sharp layer: use the quiz layer
                k = quiz.shape[0] * (0.95 / 3.0) / 2; c = quiz.shape[0] / 2
                crop = quiz[int(c - k):int(c + k), int(c - k):int(c + k)]
                hi[hole] = cv2.resize(crop, (hi.shape[1], hi.shape[0]), interpolation=cv2.INTER_LINEAR)[hole]
            hi, quiz = enhance(hi, ref), enhance(quiz, ref)
            if check is not None:
                c, half_w = quiz.shape[1] // 2, quiz.shape[1] // 6               # the quiz view: E wide, 1.78 E tall
                half_h = int(half_w * H_RATIO)
                preview = cv2.resize(quiz[c - half_h:c + half_h, c - half_w:c + half_w], (540, 960),
                                     interpolation=cv2.INTER_AREA)
                if not check(preview):
                    log(f"  {day}: rejected by the image check, next day"); continue
            info = dict(source="sentinel-2", date=day, year=day[:4], tiles=[f.get("id") for f in items],
                        provider=items[0]["_src"]["name"])
            return [Layer(hi, h_bounds, f"sentinel-2 hi {day}"), Layer(quiz, q_bounds, f"sentinel-2 quiz {day}")], info
        except (ImageryError, ImageryTrouble):
            raise
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
            log(redact(f"  {day}: could not read the scenes ({errors[-1][:300]})"))
    if errors:                     # reading failed (network / GDAL) - not the place's fault
        raise ImageryTrouble(redact(f"could not read Sentinel-2 files ({errors[-1][:300]})"))
    raise ImageryError("all candidate days were cloudy or incomplete")


def reveal_layers(lon, lat, reveal_km):
    """Blue Marble for the zoom-out: a regional and a continental layer."""
    return [bluemarble(lon, lat, max(1900.0, reveal_km * 0.6)), bluemarble(lon, lat, reveal_km * 2.0)]


# ------------------------------------------------------------------ offline demo imagery (tests, --demo)
def demo_layers(lon, lat, quiz_km, reveal_km, seed=1):
    """Procedural 'satellite-like' imagery (coast, fields, forest, mountains) so the whole pipeline can be tested
    without internet. The same world coordinates always give the same picture, so all layers fit together."""
    rng = np.random.default_rng(seed)
    bases = [rng.random((256, 256)).astype(np.float32) for _ in range(14)]

    def noise(X, Y, period, i):
        u = ((X / period) % 1.0) * 256; v = ((Y / period) % 1.0) * 256
        return cv2.remap(bases[i % len(bases)], u.astype(np.float32), v.astype(np.float32), cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_WRAP)

    def terrain(bounds, size):
        x0, y0, x1, y1 = bounds
        X, Y = np.meshgrid(np.linspace(x0, x1, size), np.linspace(y1, y0, size))
        X = X + 3.3e6 * seed; Y = Y - 1.7e6 * seed
        z = np.zeros((size, size), np.float32); amp, tot, period = 1.0, 0.0, 600e3
        px = (x1 - x0) / size
        for o in range(14):
            if period < px * 1.5:
                break
            z += amp * noise(X, Y, period, o); tot += amp
            amp *= 0.52; period /= 2.0
        z /= tot
        z = (z - 0.5) * 2.4 + 0.52                    # fixed scaling: every layer gets the same colours
        # field patchwork in the lowlands
        fx = np.floor(X / 420.0).astype(np.int64); fy = np.floor(Y / 290.0).astype(np.int64)
        h = ((fx * 73856093) ^ (fy * 19349663)) % 997 / 997.0
        img = np.zeros((size, size, 3), np.float32)
        ramp = [(0.00, (8, 24, 52)), (0.40, (20, 58, 92)), (0.46, (52, 120, 128)), (0.48, (196, 184, 140)),
                (0.52, (104, 128, 70)), (0.70, (58, 88, 44)), (0.86, (110, 98, 74)), (0.96, (150, 142, 132)),
                (1.00, (236, 238, 240))]
        zc = np.clip(z, 0, 1)
        for (a0, c0), (a1, c1) in zip(ramp, ramp[1:]):
            m = (zc >= a0) & (zc <= a1)
            k = ((zc[m] - a0) / (a1 - a0))[:, None]
            img[m] = np.array(c0) * (1 - k) + np.array(c1) * k
        low = (zc > 0.5) & (zc < 0.7)
        fields = np.stack([118 + 70 * h, 128 + 40 * h, 64 + 20 * (1 - h)], -1)
        img[low] = img[low] * 0.45 + fields[low] * 0.55
        return np.clip(img, 0, 255).astype(np.uint8)

    out = []
    for side, size, name in ((quiz_km * 0.95, 2048, "demo hi"), (quiz_km * 3.0, 3600, "demo quiz")):
        b = square_bounds(lon, lat, side)
        out.append(Layer(terrain(b, size), b, name))
    try:                                      # real coastlines for the zoom-out if Natural Earth is there
        from .borders import land_layers
        reveal = land_layers(lon, lat, reveal_km)
    except Exception:
        reveal = []
        for side in (max(1900.0, reveal_km * 0.6), reveal_km * 2.0):
            b = square_bounds(lon, lat, side)
            reveal.append(Layer(terrain(b, 1400), b, "demo bm"))
    return out, reveal, dict(source="demo", date="2026-01-01", year="2026", tiles=[])
