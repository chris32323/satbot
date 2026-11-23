"""Post the finished reel to TikTok through Zernio (a service with a TikTok-audited app).

  python -m bot.tiktok --out out --video-url URL

Works for all bots. GitHub Secrets:
  ZERNIO_API_KEY      API key from zernio.com (Settings -> API)
  TIKTOK_ACCOUNT_ID   Zernio id of the TikTok account of THIS bot
Optional variable: TIKTOK_PRIVACY (default PUBLIC_TO_EVERYONE).
If the key is missing, TikTok is skipped. If only the account id is missing, the bot prints all connected
TikTok accounts with their ids into the log, so you can copy the right one.
"""
import argparse, json, os, re, time
from pathlib import Path
import requests

try:
    from .safety import redact
except Exception:                                    # pragma: no cover
    def redact(s): return s

API = "https://zernio.com/api/v1"


def _env(name, default=""):
    v = os.getenv(name)
    return default if v is None or not v.strip() else v.strip()


def _headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _items(js):
    """Find the list of accounts in an API answer, whatever it is called."""
    if isinstance(js, list):
        return js
    for k in ("accounts", "data", "items", "results"):
        v = js.get(k) if isinstance(js, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _items(v)
            if inner:
                return inner
    return []


def list_accounts(key):
    r = requests.get(f"{API}/accounts", headers=_headers(key), timeout=60)
    if r.status_code != 200:
        raise SystemExit(redact(f"Zernio: could not list accounts: HTTP {r.status_code} {r.text[:300]}"))
    out = []
    for a in _items(r.json()):
        if str(a.get("platform", "")).lower() != "tiktok":
            continue
        out.append(dict(id=a.get("_id") or a.get("id") or a.get("accountId"),
                        name=a.get("username") or a.get("displayName") or a.get("name") or "?"))
    return out


def caption_for_tiktok(caption):
    text = re.sub(r"\s*\n\s*", " ", caption).strip()          # TikTok ignores line breaks anyway
    return text[:2150]


def post(key, account_id, video_url, caption, log=print):
    body = {
        "content": caption_for_tiktok(caption),
        "mediaItems": [{"type": "video", "url": video_url}],
        "platforms": [{"platform": "tiktok", "accountId": account_id}],
        "tiktokSettings": {
            "privacy_level": _env("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE"),
            "allow_comment": True, "allow_duet": True, "allow_stitch": True,
            "content_preview_confirmed": True, "express_consent_given": True,
        },
        "publishNow": True,
    }
    last = ""
    for attempt in range(1, 4):
        try:
            r = requests.post(f"{API}/posts", headers=_headers(key), data=json.dumps(body), timeout=300)
            if r.status_code in (200, 201):
                js = r.json() if r.text else {}
                p = js.get("post", js) if isinstance(js, dict) else {}
                url = p.get("platformPostUrl") or ""
                for pl in p.get("platforms", []) if isinstance(p, dict) else []:
                    url = url or pl.get("platformPostUrl", "")
                log(f"TikTok post sent via Zernio: id {p.get('_id') or p.get('id') or '?'} "
                    f"status {p.get('status', '?')} {url}")
                return js
            last = f"HTTP {r.status_code} {r.text[:500]}"
            if r.status_code in (400, 401, 402, 403, 404, 422):
                break                                            # wrong key / id / plan: retrying won't help
        except requests.RequestException as e:
            last = str(e)
        log(redact(f"TikTok attempt {attempt}/3 failed: {last}"))
        time.sleep(20 * attempt)
    hint = ""
    if "401" in last[:12]:
        hint = " -> ZERNIO_API_KEY is wrong or was deleted."
    elif "403" in last[:12] or "402" in last[:12]:
        hint = " -> your Zernio plan does not allow this (TikTok / number of accounts) or the account must be reconnected."
    elif "404" in last[:12]:
        hint = " -> TIKTOK_ACCOUNT_ID is wrong."
    raise SystemExit(redact(f"TikTok post failed: {last}{hint}"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out"); ap.add_argument("--video-url", default="")
    a = ap.parse_args(argv)
    key, acc = _env("ZERNIO_API_KEY"), _env("TIKTOK_ACCOUNT_ID")
    if not key:
        print("TikTok is not set up (ZERNIO_API_KEY missing) - skipped."); return
    if not acc:
        accs = list_accounts(key)
        print("TIKTOK_ACCOUNT_ID is missing. Connected TikTok accounts in Zernio:")
        for x in accs:
            print(f"  {x['name']}: {x['id']}")
        if not accs:
            print("  (none - connect your TikTok account in Zernio first)")
        print("Copy the id of THIS bot's account into the secret TIKTOK_ACCOUNT_ID. Skipped for now.")
        return
    if not a.video_url:
        raise SystemExit("no --video-url given")
    caption = (Path(a.out) / "caption.txt").read_text(encoding="utf-8")
    js = post(key, acc, a.video_url, caption)
    (Path(a.out) / "tiktok.json").write_text(json.dumps(js, indent=2)[:20000], encoding="utf-8")


if __name__ == "__main__":
    main()
