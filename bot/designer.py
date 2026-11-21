"""One-time design review by OpenAI (same idea as in the GeoGuess bot).

Renders 5 real still frames (title, satellite view + timer, "lock in your guess", reveal with answer card,
end card), sends them together with the current design values to an OpenAI vision model and lets it improve the
values (colours, sizes, positions, transparency) - never code. Every value is clamped to safe limits and overlaps
are fixed automatically. Two rounds: improve, then look at the result and refine. The result is saved to
data/design.json and used for every video from then on. Delete that file to go back to the original design.
"""
import base64, io, json, os, time
from pathlib import Path
import requests
from PIL import Image

from . import design as D
from .safety import redact

DESIGN_MODEL = os.getenv("DESIGN_MODEL") or os.getenv("VISION_MODEL") or "gpt-5-mini"
ROUNDS = int(os.getenv("DESIGN_ROUNDS") or 2)

BRIEF = """You are a senior motion and UI designer for short-form vertical video (Instagram Reels, TikTok, 1080x1920).
You review a daily geography quiz reel ("Where on Earth?"): real satellite imagery (Sentinel-2) is shown from above
while the camera slowly drifts, viewers guess the place before a countdown runs out, then the camera zooms out to a
continent view (NASA Blue Marble) with country borders, a pin and a 200 km circle, followed by an answer card and an
end card.

The 5 images are, in order: 1) title screen "WHERE ON EARTH?", 2) satellite view with countdown timer and scale bar,
3) satellite view with timer and "LOCK IN YOUR GUESS", 4) continent view with pin + answer card, 5) end card.
The satellite images themselves cannot be changed - only the overlay design.

Goals: premium, modern, clean, very readable on a phone in one second, strong contrast, consistent style,
pleasant colours that work on any satellite image (ocean, desert, snow, green fields, dense cities). Respect the
Instagram safe zones: the bottom ~250 px are partly covered by the caption and the right ~140 px by the
like/comment buttons.

You may ONLY change these design values (current values and allowed ranges are given). Colours are hex.
Reply with JSON only:
{"theme": {only the keys you change: new value}, "changes": ["short reason per change"], "verdict": "one sentence"}
If the design is already very good, return an empty "theme" and say so in "verdict".
"""


def _jpeg_b64(img, w=540):
    im = img.convert("RGB"); im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=88)
    return base64.b64encode(b.getvalue()).decode()


def key_frames(rounds, borders):
    """The 5 typical screens of a reel, rendered with the current design (no voice needed)."""
    from .render import Reel
    return Reel(rounds, borders=borders, tts=None).key_frames()


def contact_sheet(frames, path, w=432):
    small = [f.resize((w, int(f.height * w / f.width)), Image.LANCZOS) for f in frames]
    sheet = Image.new("RGB", (w * len(small) + 12 * (len(small) - 1), small[0].height), (20, 20, 24))
    for i, f in enumerate(small):
        sheet.paste(f, (i * (w + 12), 0))
    sheet.save(path, quality=90)


def ask(frames, theme, note=""):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    limits = {k: (v if isinstance(v, tuple) else ("hex" if v == "hex" else "true/false")) for k, v in D.LIMITS.items()}
    text = (BRIEF + f"\nCurrent design values:\n{json.dumps(theme, indent=1)}\n"
            f"Allowed ranges (min, max):\n{json.dumps(limits)}\n" + note)
    content = [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_jpeg_b64(f)}", "detail": "high"}}
        for f in frames]
    for attempt in range(3):
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", timeout=300,
                              headers={"Authorization": f"Bearer {key}"},
                              json=dict(model=DESIGN_MODEL, messages=[{"role": "user", "content": content}],
                                        response_format={"type": "json_object"}, max_completion_tokens=8000))
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"].get("content") or "{}"
                usage = r.json().get("usage", {})
                print(f"  OpenAI ({DESIGN_MODEL}): {usage.get('prompt_tokens', '?')} in / "
                      f"{usage.get('completion_tokens', '?')} out tokens")
                return json.loads(msg)
            print(redact(f"  OpenAI HTTP {r.status_code}: {r.text[:300]}"))
            if r.status_code in (400, 401, 403, 404):
                break
        except Exception as e:
            print(redact(f"  OpenAI error: {e}"))
        time.sleep(5)
    return None


def run(rounds, borders, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing - the design run needs OpenAI")
    theme = D.load_theme()
    D.apply_theme(theme)
    frames = key_frames(rounds, borders)
    contact_sheet(frames, out / "design_before.jpg")
    report = [f"design run with {DESIGN_MODEL}"]
    for rnd in range(1, ROUNDS + 1):
        note = ("" if rnd == 1 else
                "\nThis is round 2: the images already show your previous changes. Check them critically "
                "(readability, balance, contrast, overlaps) and refine. Return an empty theme if it is now good.")
        print(f"design round {rnd}: asking OpenAI ...")
        ans = ask(frames, theme, note)
        if not ans:
            report.append(f"round {rnd}: no answer from OpenAI - keeping the current design"); break
        changes = ans.get("theme") or {}
        report.append(f"round {rnd} verdict: {ans.get('verdict', '')}")
        report += [f"  - {c}" for c in ans.get("changes", [])]
        if not changes:
            report.append(f"round {rnd}: no further changes"); break
        new, notes = D.clean_theme({**theme, **changes})
        report += [f"  (safety) {n}" for n in notes]
        diff = {k: (theme[k], new[k]) for k in new if new[k] != theme.get(k)}
        report += [f"  {k}: {a} -> {b}" for k, (a, b) in diff.items()]
        theme = new
        D.apply_theme(theme)
        frames = key_frames(rounds, borders)
    contact_sheet(frames, out / "design_after.jpg")
    saved = {k: v for k, v in theme.items() if v != D.DEFAULT_THEME.get(k)}
    D.THEME_FILE.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report.append(f"saved {len(saved)} changed values to data/design.json")
    (out / "design_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
