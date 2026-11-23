"""Upload the finished reel (out/reel.mp4 + out/caption.txt) to YouTube as a Short.

  python -m bot.youtube --out out

Works for both bots (GeoGuess + satellite). Needs three GitHub Secrets:
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   (see the tutorial)
Optional variables: YT_PRIVACY (public / unlisted / private, default public), YT_CATEGORY (default 27 = Education),
YT_TITLE (fixed title instead of the first caption line).
If the secrets are missing, the upload is skipped (the run stays green).
"""
import argparse, json, os, re, time
from pathlib import Path
import requests

try:
    from .safety import redact
except Exception:                                    # pragma: no cover
    def redact(s): return s

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _env(name, default=""):
    v = os.getenv(name)
    return default if v is None or not v.strip() else v.strip()


def access_token(client_id, client_secret, refresh_token):
    r = requests.post(TOKEN_URL, timeout=60, data=dict(client_id=client_id, client_secret=client_secret,
                                                        refresh_token=refresh_token, grant_type="refresh_token"))
    if r.status_code != 200:
        hint = ""
        if "invalid_grant" in r.text:
            hint = (" -> the refresh token is no longer valid. Typical reason: the Google Cloud app is still in "
                    "'Testing' (tokens expire after 7 days). Set it to 'In production' and create a new token.")
        raise SystemExit(redact(f"YouTube login failed: HTTP {r.status_code} {r.text[:300]}{hint}"))
    return r.json()["access_token"]


def _clean(text):
    return text.replace("<", "").replace(">", "")


def metadata(caption):
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    title = _env("YT_TITLE") or (lines[0] if lines else "Daily geography quiz")
    title = re.sub(r"\s+", " ", _clean(title))
    if "#shorts" not in title.lower():
        title = (title[:91].rstrip() + " #Shorts") if len(title) > 91 else title + " #Shorts"
    tags, total = [], 0
    for t in re.findall(r"#(\w+)", caption):
        if t.lower() != "shorts" and t not in tags and total + len(t) + 1 <= 450:
            tags.append(t); total += len(t) + 1
    desc = _clean(caption)
    if "#shorts" not in desc.lower():
        desc += "\n\n#Shorts"
    return {
        "snippet": {"title": title[:100], "description": desc[:4900], "tags": tags,
                    "categoryId": _env("YT_CATEGORY", "27")},
        "status": {"privacyStatus": _env("YT_PRIVACY", "public"), "selfDeclaredMadeForKids": False,
                   "embeddable": True},
    }


def upload(video, caption, client_id, client_secret, refresh_token, log=print):
    token = access_token(client_id, client_secret, refresh_token)
    meta = metadata(caption)
    size = Path(video).stat().st_size
    for attempt in range(1, 4):
        try:
            r = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, timeout=60,
                              headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                       "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                              data=json.dumps(meta))
            if r.status_code != 200 or "Location" not in r.headers:
                raise RuntimeError(f"start of upload failed: HTTP {r.status_code} {r.text[:400]}")
            with open(video, "rb") as f:
                r = requests.put(r.headers["Location"], data=f, timeout=900,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                                          "Content-Length": str(size)})
            if r.status_code in (200, 201):
                vid = r.json()
                status = vid.get("status", {})
                log(f"YouTube upload ok: https://youtube.com/shorts/{vid['id']} "
                    f"(privacy: {status.get('privacyStatus')}, upload status: {status.get('uploadStatus')})")
                return vid
            raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:400]}")
        except Exception as e:
            msg = redact(str(e))
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg or ("HTTP 4" in msg and "HTTP 429" not in msg):
                raise SystemExit(f"YouTube: {msg}")
            log(f"YouTube attempt {attempt}/3: {msg}")
            time.sleep(20 * attempt)
    raise SystemExit("YouTube upload failed 3 times")


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="out")
    a = ap.parse_args(argv)
    cid, sec, ref = _env("YT_CLIENT_ID"), _env("YT_CLIENT_SECRET"), _env("YT_REFRESH_TOKEN")
    if not (cid and sec and ref):
        print("YouTube is not set up (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN missing) - skipped.")
        return
    out = Path(a.out)
    vid = upload(out / "reel.mp4", (out / "caption.txt").read_text(encoding="utf-8"), cid, sec, ref)
    (out / "youtube.json").write_text(json.dumps({"id": vid.get("id")}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
