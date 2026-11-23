"""Keep secrets out of logs and untrusted text out of posts."""
import os, re

_SECRET_ENV = ("MAPILLARY_TOKEN", "IG_ACCESS_TOKEN", "OPENAI_API_KEY", "GH_PAT")


def redact(text):
    """Remove any token from text before it is printed (GitHub masks secrets too; this is a second layer)."""
    s = str(text)
    for k in _SECRET_ENV:
        v = os.getenv(k, "")
        if len(v) >= 8:
            s = s.replace(v, "***")
    s = re.sub(r"(access_token=)[^&\s'\"]+", r"\1***", s)
    s = re.sub(r"(Bearer |OAuth )[A-Za-z0-9|._\-]+", r"\1***", s)
    return s


def clean_username(name, limit=30):
    """Mapillary usernames go into the public caption: allow only plain characters, no links or emojis."""
    return re.sub(r"[^A-Za-z0-9._-]", "", str(name or ""))[:limit]
