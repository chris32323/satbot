"""AI image check (OpenAI vision) for the satellite quiz view: no clouds/haze/stripes, not empty, interesting.

Only used when OPENAI_API_KEY is set (and VISION_CHECK is not "0"). If the API fails, the bot continues without
the check, so a post is never lost because of it.
"""
import base64, io, json, os
import requests
from PIL import Image
from .safety import redact

VISION_MODEL = (os.getenv("VISION_MODEL") or "").strip() or "gpt-5-mini"
ENABLED = bool((os.getenv("OPENAI_API_KEY") or "").strip()) and (os.getenv("VISION_CHECK") or "1").strip() != "0"


def _b64(img, max_side=1024):
    im = img.copy() if isinstance(img, Image.Image) else Image.fromarray(img)
    im = im.convert("RGB"); im.thumbnail((max_side, max_side))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def check(view, place):
    """Return dict(usable, interesting, reason) or None if the check could not run."""
    if not ENABLED:
        return None
    task = (f"This is a top-down satellite image (true colour, about 10 m per pixel) of an area near: {place}.\n"
            "It will be used in a geography quiz where viewers guess the location.\n"
            "Answer ONLY with JSON: "
            '{"usable": true/false, "interesting": true/false, "reason": "max 12 words"}\n'
            "- usable = false if clouds, haze or smoke cover more than about 10 %, if there are no-data gaps, "
            "black areas, obvious mosaic seams or stripes, or if the image is very dark or almost featureless.\n"
            "- interesting = true if there are clear, characteristic features (coastline, rivers, mountains, "
            "city layout, fields, islands, desert patterns).")
    content = [{"type": "text", "text": task},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(view)}", "detail": "low"}}]
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
                          headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                          json=dict(model=VISION_MODEL, messages=[{"role": "user", "content": content}],
                                    response_format={"type": "json_object"}, max_completion_tokens=2000),
                          timeout=120)
        if r.status_code != 200:
            print(redact(f"  AI check unavailable (HTTP {r.status_code}: {r.text[:200]}) - continuing without it"))
            return None
        res = json.loads(r.json()["choices"][0]["message"]["content"] or "{}")
        return dict(usable=bool(res.get("usable", True)), interesting=bool(res.get("interesting", True)),
                    reason=str(res.get("reason", ""))[:120])
    except Exception as e:
        print(redact(f"  AI check failed ({e}) - continuing without it"))
        return None
