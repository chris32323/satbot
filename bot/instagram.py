"""Publish a Reel with the Instagram API (Instagram Login) and refresh the long-lived token."""
import time
import requests
from .safety import redact
from .config import IG_API, IG_USER_ID, IG_ACCESS_TOKEN


class IGError(RuntimeError):
    pass


def _check(r):
    try:
        j = r.json()
    except ValueError:
        raise IGError(redact(f"HTTP {r.status_code}: {r.text[:300]}"))
    if r.status_code >= 400 or "error" in j:
        raise IGError(redact(f"HTTP {r.status_code}: {j.get('error', j)}"))
    return j


def api_for(token):
    """Instagram-Login tokens (IG...) use graph.instagram.com, Facebook-Login tokens (EAA...) use graph.facebook.com."""
    if token.startswith("EAA"):
        return IG_API.replace("graph.instagram.com", "graph.facebook.com")
    return IG_API


def publish_reel(video_url, caption, user_id=IG_USER_ID, token=IG_ACCESS_TOKEN, max_wait_s=900):
    if not (user_id and token):
        raise IGError("IG_USER_ID / IG_ACCESS_TOKEN not set")
    print(f"  token type: {'Facebook login' if token.startswith('EAA') else 'Instagram login'}, length {len(token)}")
    IG_API = api_for(token)
    print("  creating reel container ...")
    j = _check(requests.post(f"{IG_API}/{user_id}/media", data=dict(
        media_type="REELS", video_url=video_url, caption=caption, share_to_feed="true", access_token=token), timeout=120))
    cid = j["id"]
    t0 = time.time(); status = None
    while time.time() - t0 < max_wait_s:
        time.sleep(20)
        s = _check(requests.get(f"{IG_API}/{cid}", params=dict(fields="status_code,status", access_token=token), timeout=60))
        status = s.get("status_code"); print(f"  container status: {status}")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise IGError(f"container {status}: {s.get('status')}")
    else:
        raise IGError(f"container not ready after {max_wait_s}s (last status {status})")
    for attempt in range(3):
        try:
            p = _check(requests.post(f"{IG_API}/{user_id}/media_publish",
                                     data=dict(creation_id=cid, access_token=token), timeout=120))
            break
        except IGError as e:
            if attempt == 2: raise
            print(redact(f"  publish retry ({e})")); time.sleep(30)
    mid = p["id"]
    try:
        info = _check(requests.get(f"{IG_API}/{mid}", params=dict(fields="permalink", access_token=token), timeout=60))
        return mid, info.get("permalink")
    except IGError:
        return mid, None


def refresh_token(token=IG_ACCESS_TOKEN):
    """Long-lived tokens last 60 days; refreshing returns a new 60-day token."""
    j = _check(requests.get("https://graph.instagram.com/refresh_access_token",
                            params=dict(grant_type="ig_refresh_token", access_token=token), timeout=60))
    return j["access_token"], j.get("expires_in")
