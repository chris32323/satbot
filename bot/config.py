"""Central settings for the satellite quiz bot. Everything can be overridden with environment variables
(GitHub Secrets / Variables). An empty variable counts as "not set"."""
import os
from pathlib import Path


def _env(name, default):
    v = os.getenv(name)
    return default if v is None or not v.strip() else v.strip()


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
NE_DIR = Path(_env("NE_DIR", str(DATA / "naturalearth")))
CACHE = Path(_env("IMAGE_CACHE", str(ROOT / "cache")))          # downloaded imagery (not committed)

FONT_BOLD = str(ASSETS / "fonts" / "Poppins-Bold.ttf")
FONT_MEDIUM = str(ASSETS / "fonts" / "Poppins-Medium.ttf")

# ---- video
W, H, FPS = 1080, 1920, 30
ROUNDS_PER_REEL = int(_env("ROUNDS_PER_REEL", "2"))
RADIUS_KM = int(float(_env("RADIUS_KM", "200")))                 # circle around the answer
TITLE = _env("TITLE", "WHERE ON EARTH?")
TITLE_EDITION = _env("TITLE_EDITION", "Satellite Edition")

# ---- imagery
STAC_URL = _env("STAC_URL", "https://earth-search.aws.element84.com/v1/search")
S2_COLLECTIONS = [c.strip() for c in _env("S2_COLLECTIONS", "sentinel-2-c1-l2a,sentinel-2-l2a").split(",") if c.strip()]
S2_MAX_CLOUD = float(_env("S2_MAX_CLOUD", "15"))                 # % cloud cover of the whole scene
PC_STAC_URL = _env("PC_STAC_URL", "https://planetarycomputer.microsoft.com/api/stac/v1/search")   # backup source
PC_TOKEN_URL = _env("PC_TOKEN_URL", "https://planetarycomputer.microsoft.com/api/sas/v1/token")
GIBS_WMS = _env("GIBS_WMS", "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi")
GIBS_LAYERS = [c.strip() for c in _env("GIBS_LAYERS", "BlueMarble_NextGeneration,BlueMarble_ShadedRelief_Bathymetry")
               .split(",") if c.strip()]
REVEAL_KM = float(_env("REVEAL_KM", "3200"))                     # width of the final zoomed-out view

# ---- voice: ElevenLabs only (same voice and settings as the GeoGuess bot)
ELEVEN_MODEL = _env("ELEVEN_MODEL", "eleven_v4")
ELEVEN_VOICE_ID = _env("ELEVEN_VOICE_ID", "")
ELEVEN_VOICE_NAME = _env("ELEVEN_VOICE_NAME", "Nick - Relatable & Engaging Friend")
ELEVEN_STABILITY = float(_env("ELEVEN_STABILITY", "0.50"))
ELEVEN_SIMILARITY = float(_env("ELEVEN_SIMILARITY", "0.58"))
ELEVEN_FORMAT = _env("ELEVEN_FORMAT", "mp3_44100_128")
REQUIRE_VOICE = _env("REQUIRE_VOICE", "1") == "1"
VOICE_PITCH = float(_env("VOICE_PITCH", "1.0"))
VOICE_PAUSE = float(_env("VOICE_PAUSE", "0.6"))                  # seconds of silence between sentences

# ---- Instagram (Instagram API with Instagram Login, or a Facebook EAA token)
IG_API = _env("IG_API", "https://graph.instagram.com/v26.0")
IG_USER_ID = _env("IG_USER_ID", "").strip('"').strip()
IG_ACCESS_TOKEN = "".join(_env("IG_ACCESS_TOKEN", "").split()).strip('"\'')

HASHTAGS = _env("HASHTAGS", "#geography #satellite #geoguessr #quiz #earth #maps #guessthelocation #fromabove")
