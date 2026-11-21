"""Visual design of the reel: dark, premium look with frosted-glass panels and a mint accent.
All elements are drawn in code (sharp at any size, animated, no cost per video)."""
import math
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .config import FONT_BOLD, FONT_MEDIUM

# ------------------------------------------------------------------ theme (all design values in one place)
# data/design.json (written once by the OpenAI design run) overrides these defaults.
import json as _json
from pathlib import Path as _Path

THEME_FILE = _Path(__file__).resolve().parent.parent / "data" / "design.json"
DEFAULT_THEME = {
    "accent": "#3EE0A0", "warn": "#FFC446", "danger": "#FF4C62", "ink": "#090D1A",
    "glass_tint": "#0C1222", "muted": "#ACB6CC",
    "glass_alpha": 165, "glass_blur": 16, "glass_border_alpha": 46,
    "vignette_top": 150, "vignette_bottom": 190, "intro_dim": 0.62,
    "title_size": 150, "title_y": 960, "show_edition_chip": True, "show_title_underline": True,
    "hud_y": 58,
    "timer_radius": 96, "timer_ring": 13, "timer_y": 300,
    "card_y": 1640, "card_height": 268, "card_radius": 36, "place_size": 64, "fact_size": 32,
    "map_border_alpha": 235,
    "end_title_size": 92, "cta_text": "FOLLOW FOR A DAILY ROUND",
}
# allowed range per value: (min, max) for numbers, "hex" for colours, bool, ("str", max_len)
LIMITS = {
    "accent": "hex", "warn": "hex", "danger": "hex", "ink": "hex", "glass_tint": "hex", "muted": "hex",
    "glass_alpha": (90, 230), "glass_blur": (0, 30), "glass_border_alpha": (0, 120),
    "vignette_top": (0, 230), "vignette_bottom": (0, 230), "intro_dim": (0.35, 0.9),
    "title_size": (100, 170), "title_y": (760, 1120), "show_edition_chip": bool, "show_title_underline": bool,
    "hud_y": (40, 130),
    "timer_radius": (70, 125), "timer_ring": (8, 20), "timer_y": (200, 360),
    "card_y": (1590, 1740), "card_height": (220, 310), "card_radius": (12, 60), "place_size": (48, 80),
    "fact_size": (24, 38), "map_border_alpha": (0, 255),
    "end_title_size": (70, 110), "cta_text": ("str", 28),
}


def _hex(c):
    c = c.strip().lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def clean_theme(t):
    """Keep only known keys, clamp every value into its allowed range, fix overlaps. Returns (theme, notes)."""
    out, notes = dict(DEFAULT_THEME), []
    for k, v in (t or {}).items():
        lim = LIMITS.get(k)
        if lim is None:
            notes.append(f"ignored unknown '{k}'"); continue
        try:
            if lim == "hex":
                _hex(str(v)); out[k] = "#" + str(v).strip().lstrip("#")[:6].upper()
            elif lim is bool:
                out[k] = bool(v)
            elif isinstance(lim, tuple) and lim[0] == "str":
                out[k] = str(v).strip().upper()[: lim[1]] or DEFAULT_THEME[k]
            else:
                x = float(v); y = min(max(x, lim[0]), lim[1])
                if y != x: notes.append(f"{k} {x:g} -> {y:g} (limit)")
                out[k] = int(round(y)) if isinstance(DEFAULT_THEME[k], int) else y
        except Exception:
            notes.append(f"bad value for '{k}' kept default")
    if out["timer_y"] + out["timer_radius"] > 450:                         # timer must stay above the map
        out["timer_y"] = 450 - out["timer_radius"]; notes.append("timer moved up (map overlap)")
    need = out["hud_y"] + 128                                               # round text + bars + stars
    if out["timer_y"] - out["timer_radius"] < need:                         # ... and below round bar + stars
        out["timer_y"] = min(need + out["timer_radius"], 450 - out["timer_radius"]); notes.append("timer moved down (stars)")
        if out["timer_y"] - out["timer_radius"] < need:
            out["hud_y"] = max(40, out["timer_y"] - out["timer_radius"] - 128); notes.append("round bar moved up")
    top = out["card_y"] - out["card_height"] / 2
    if top < 1450: out["card_y"] = int(1450 + out["card_height"] / 2); notes.append("card moved down (map overlap)")
    if out["card_y"] + out["card_height"] / 2 > 1890:
        out["card_y"] = int(1890 - out["card_height"] / 2); notes.append("card moved up (screen edge)")
    return out, notes


def load_theme():
    try:
        return clean_theme(_json.loads(THEME_FILE.read_text()))[0] if THEME_FILE.exists() else dict(DEFAULT_THEME)
    except Exception:
        return dict(DEFAULT_THEME)


T = {}


def apply_theme(theme):
    """Set the module-wide design values (colours as RGB tuples)."""
    global INK, GLASS, ACC, AMBER, RED, MUTED
    T.clear(); T.update(theme)
    INK, GLASS, ACC = _hex(theme["ink"]), _hex(theme["glass_tint"]), _hex(theme["accent"])
    AMBER, RED, MUTED = _hex(theme["warn"]), _hex(theme["danger"]), _hex(theme["muted"])


WHITE = (255, 255, 255)
apply_theme(load_theme())

_fonts = {}


def font(bold, size):
    k = (bold, int(size))
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(FONT_BOLD if bold else FONT_MEDIUM, max(int(size), 1))
    return _fonts[k]


def clamp01(t): return min(max(t, 0.0), 1.0)
def ease_out(t): return 1 - (1 - clamp01(t)) ** 3


# ------------------------------------------------------------------ text helpers
def tracked_width(d, text, fnt, track):
    return sum(d.textlength(c, font=fnt) for c in text) + track * max(len(text) - 1, 0)


def text_tracked(d, cx, y, text, fnt, fill, track=6):
    """Letter-spaced text, centred on cx (used for small caps labels)."""
    x = cx - tracked_width(d, text, fnt, track) / 2
    for c in text:
        d.text((x, y), c, font=fnt, fill=fill)
        x += d.textlength(c, font=fnt) + track


def text_center(d, cx, y, text, fnt, fill):
    d.text((cx - d.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=fill)


def fit_font(d, text, bold, size, max_w, min_size=20):
    while size > min_size and d.textlength(text, font=font(bold, size)) > max_w:
        size -= 2
    return font(bold, size)


def soft_text(base, cx, y, text, fnt, fill=WHITE, shadow=150, blur=10):
    """Big headline with a soft dark glow behind it instead of a hard outline."""
    d0 = ImageDraw.Draw(base)
    tw = d0.textlength(text, font=fnt); asc, desc = fnt.getmetrics()
    pad = blur * 3
    sh = Image.new("RGBA", (int(tw) + 2 * pad, asc + desc + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((pad, pad), text, font=fnt, fill=(0, 0, 0, shadow))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(sh, (int(cx - tw / 2 - pad), int(y - pad + blur * 0.4)))
    d0.text((cx - tw / 2, y), text, font=fnt, fill=fill)


# ------------------------------------------------------------------ glass panels
_mask_cache, _shadow_cache = {}, {}


def _rmask(w, h, r):
    k = (w, h, r)
    if k not in _mask_cache:
        m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
        _mask_cache[k] = m
    return _mask_cache[k]


def _shadow(w, h, r, strength=110, blur=22):
    k = (w, h, r, strength, blur)
    if k not in _shadow_cache:
        pad = blur * 3
        s = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(s).rounded_rectangle([pad, pad + blur // 2, pad + w, pad + h + blur // 2], radius=r,
                                            fill=(0, 0, 0, strength))
        _shadow_cache[k] = (s.filter(ImageFilter.GaussianBlur(blur)), pad)
    return _shadow_cache[k]


def glass(base, box, radius=28, tint=None, alpha=None, blur=None, shadow=True, border=None, alpha_mul=1.0):
    """Frosted-glass panel: blurred, darkened copy of what is behind it, soft shadow, hairline border."""
    tint = GLASS if tint is None else tint
    alpha = T["glass_alpha"] if alpha is None else alpha
    blur = T["glass_blur"] if blur is None else blur
    border = T["glass_border_alpha"] if border is None else border
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    W, H = base.size
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    w, h = x1 - x0, y1 - y0
    if w < 4 or h < 4 or alpha_mul <= 0.01:
        return
    r = min(radius, h // 2, w // 2)
    if shadow:
        s, pad = _shadow(w, h, r)
        if alpha_mul < 1:
            s = s.copy(); s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha_mul)))
        base.alpha_composite(s, (x0 - pad, y0 - pad))
    region = base.crop((x0, y0, x1, y1))
    if blur > 0: region = region.filter(ImageFilter.GaussianBlur(blur))
    region.alpha_composite(Image.new("RGBA", (w, h), tint + (alpha,)))
    hl = ImageDraw.Draw(region)
    hl.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, outline=(255, 255, 255, border), width=2)
    hl.line([(r, 1), (w - r, 1)], fill=(255, 255, 255, min(border + 40, 255)), width=1)   # top highlight
    m = _rmask(w, h, r)
    if alpha_mul < 1:
        m = m.point(lambda v: int(v * alpha_mul))
    base.paste(region, (x0, y0), m)


def glass_pill(base, cx, y, text, fnt, fg=WHITE, track=0, pad=(34, 16), dot=None, alpha_mul=1.0):
    """Centred glass pill with optional coloured status dot. y = top of the pill."""
    d = ImageDraw.Draw(base)
    tw = tracked_width(d, text, fnt, track) if track else d.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics(); th = asc + desc
    extra = 30 if dot else 0
    w = tw + 2 * pad[0] + extra; h = th + 2 * pad[1] - 8
    x0 = cx - w / 2
    glass(base, (x0, y, x0 + w, y + h), radius=int(h / 2), alpha_mul=alpha_mul)
    tx = x0 + pad[0] + extra
    if dot:
        dc = (x0 + pad[0] + 8, y + h / 2)
        d.ellipse([dc[0] - 8, dc[1] - 8, dc[0] + 8, dc[1] + 8], fill=dot + (int(255 * alpha_mul),))
    col = fg + (int(255 * alpha_mul),)
    if track:
        text_tracked(d, tx + tw / 2, y + pad[1] - 6, text, fnt, col, track)
    else:
        d.text((tx, y + pad[1] - 6), text, font=fnt, fill=col)
    return h


# ------------------------------------------------------------------ HUD: round indicator
STARS = {"easy": 1, "medium": 2, "hard": 3}


def _star(d, cx, cy, r, filled):
    """Five-pointed star drawn as a polygon (no font needed). White only: filled = earned, outline = not."""
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    if filled:
        d.polygon(pts, fill=(255, 255, 255, 245))
    else:
        d.polygon(pts, outline=(255, 255, 255, 150), width=3)


def round_hud(base, rnd, total, W, difficulty=None):
    d = ImageDraw.Draw(base)
    hy = T["hud_y"]
    text_tracked(d, W / 2, hy, f"ROUND {rnd} OF {total}", font(True, 26), WHITE + (235,), track=5)
    bw, gap = 64, 12
    x = W / 2 - (total * bw + (total - 1) * gap) / 2
    for i in range(total):
        col = ACC + (255,) if i == rnd - 1 else (255, 255, 255, 150) if i < rnd - 1 else (255, 255, 255, 55)
        d.rounded_rectangle([x, hy + 46, x + bw, hy + 54], radius=4, fill=col)
        x += bw + gap
    n = STARS.get(difficulty or "", 0)
    if n:                                                   # difficulty: 1-3 white stars, no colour
        r, step, cy = 16, 44, hy + 92
        for i in range(3):
            _star(d, W / 2 + (i - 1) * step, cy, r, i < n)


# ------------------------------------------------------------------ timer
def timer_color(p):
    return ACC if p > 0.5 else AMBER if p > 0.25 else RED


def timer(base, cx, cy, remain, total, frac):
    """Glass disc with a thick progress ring and rounded ends; red glow pulse in the last seconds."""
    p = clamp01(max(remain, 0) / total)
    col = timer_color(p)
    urgent = remain <= 2.0 + 1e-6
    pulse = 1 + (0.07 if urgent else 0.035) * max(0.0, 1 - frac * 3.5)
    R = T["timer_radius"] * pulse
    if urgent:
        g = Image.new("RGBA", (int(R * 3), int(R * 3)), (0, 0, 0, 0))
        a = int(150 * max(0.0, 1 - frac * 1.6))
        ImageDraw.Draw(g).ellipse([R * 0.5, R * 0.5, R * 2.5, R * 2.5], fill=RED + (a,))
        g = g.filter(ImageFilter.GaussianBlur(22))
        base.alpha_composite(g, (int(cx - R * 1.5), int(cy - R * 1.5)))
    glass(base, (cx - R, cy - R, cx + R, cy + R), radius=int(R))
    d = ImageDraw.Draw(base)
    wdt = T["timer_ring"]; ring = R - 5 - wdt
    box = [cx - ring, cy - ring, cx + ring, cy + ring]
    d.arc(box, 0, 360, fill=(255, 255, 255, 42), width=wdt)
    if p > 0.002:
        a0, a1 = -90, -90 + 360 * p
        d.arc(box, a0, a1, fill=col + (255,), width=wdt)
        for ang in (a0, a1):                                       # rounded caps
            rr = ring - wdt / 2 + 0.5
            ex, ey = cx + rr * math.cos(math.radians(ang)), cy + rr * math.sin(math.radians(ang))
            d.ellipse([ex - wdt / 2, ey - wdt / 2, ex + wdt / 2, ey + wdt / 2], fill=col + (255,))
    n = str(max(math.ceil(remain - 1e-6), 0))
    f = font(True, R * 0.81)
    asc, desc = f.getmetrics()
    d.text((cx - d.textlength(n, font=f) / 2, cy - asc * 0.62), n, font=f, fill=WHITE)


# ------------------------------------------------------------------ map pin
def pin(d, x, y, color=RED, alpha=255, pulse_t=None):
    """Teardrop pin with white ring. (The pulse ring was removed on request - pulse_t is ignored.)"""
    if False:
        k = (pulse_t % 1.4) / 1.4
        rr = 18 + 70 * k; a = int(170 * (1 - k))
        d.ellipse([x - rr, y - rr * 0.45, x + rr, y + rr * 0.45], outline=color + (a,), width=4)
    r = 27; hy = y - 58
    d.ellipse([x - 15, y - 6, x + 15, y + 6], fill=(0, 0, 0, 80))
    d.polygon([(x - r * 0.8, hy + r * 0.58), (x + r * 0.8, hy + r * 0.58), (x, y)], fill=color + (alpha,))
    d.ellipse([x - r, hy - r, x + r, hy + r], fill=color + (alpha,), outline=(255, 255, 255, alpha), width=5)
    d.ellipse([x - 9, hy - 9, x + 9, hy + 9], fill=(255, 255, 255, alpha))


# ------------------------------------------------------------------ answer card
def answer_card(base, W, cy, place, fact, k):
    """Dark glass card sliding up: small mint label, big place name, muted fact line."""
    if k <= 0:
        return
    e = ease_out(k)
    cw, ch = W - 120, T["card_height"]
    y0 = cy - ch / 2 + 70 * (1 - e)
    glass(base, (60, y0, 60 + cw, y0 + ch), radius=T["card_radius"], alpha=min(T["glass_alpha"] + 35, 240), alpha_mul=e)
    d = ImageDraw.Draw(base)
    a = int(255 * e)
    d.rounded_rectangle([60 + 26, y0 + 34, 60 + 34, y0 + ch - 34], radius=4, fill=ACC + (a,))   # accent bar
    tx = 60 + 64; maxw = cw - 100
    lab = font(True, 26)
    x = tx
    for c in "THE ANSWER":
        d.text((x, y0 + 36), c, font=lab, fill=ACC + (a,)); x += d.textlength(c, font=lab) + 5
    ps = T["place_size"]
    f1 = fit_font(d, place, True, ps, maxw, 34)
    d.text((tx, y0 + 74 + (ps - f1.size) * 0.5), place, font=f1, fill=WHITE + (a,))
    if fact:
        f2 = fit_font(d, fact, False, T["fact_size"], maxw, 20)
        d.text((tx, y0 + ch - 58 - f2.size * 0.6), fact, font=f2, fill=MUTED + (a,))


# ------------------------------------------------------------------ end card
def end_card(base, W, H, t, n_rounds):
    """'How many did you get right?' + score chips + follow button, staggered in."""
    k = ease_out(t / 0.5)
    shade = Image.new("RGBA", (W, H), INK + (int(215 * k),))
    base.alpha_composite(shade)
    d = ImageDraw.Draw(base)
    if t > 0.12:
        e = ease_out((t - 0.12) / 0.45); a = int(255 * e); dy = 40 * (1 - e)
        text_tracked(d, W / 2, 640 + dy, "FINAL SCORE", font(True, 30), ACC + (a,), track=8)
        es = T["end_title_size"]; f = font(True, es)
        text_center(d, W / 2, 700 + dy, "How many did", f, WHITE + (a,))
        l1, l2 = "you get ", "right?"
        w1, w2 = d.textlength(l1, font=f), d.textlength(l2, font=f)
        x = W / 2 - (w1 + w2) / 2
        d.text((x, 700 + es * 1.22 + dy), l1, font=f, fill=WHITE + (a,))
        d.text((x + w1, 700 + es * 1.22 + dy), l2, font=f, fill=ACC + (a,))
    if t > 0.55:
        e = ease_out((t - 0.55) / 0.45); a = int(255 * e)
        text_tracked(d, W / 2, 1000, "COMMENT YOUR SCORE", font(True, 26), MUTED + (a,), track=6)
        chips = [f"{i}/{n_rounds}" for i in range(n_rounds + 1)]
        cw, gap = 150, 22
        x = W / 2 - (len(chips) * cw + (len(chips) - 1) * gap) / 2
        for i, c in enumerate(chips):
            ei = ease_out((t - 0.55 - i * 0.08) / 0.35)
            if ei <= 0: x += cw + gap; continue
            yy = 1050 + 30 * (1 - ei)
            glass(base, (x, yy, x + cw, yy + 96), radius=26, alpha=170, alpha_mul=ei)
            d = ImageDraw.Draw(base)
            col = ACC if i == n_rounds else WHITE
            text_center(d, x + cw / 2, yy + 18, c, font(True, 46), col + (int(255 * ei),))
            x += cw + gap
    if t > 1.1:
        e = ease_out((t - 1.1) / 0.45); a = int(255 * e)
        bw, bh = 700, 108; y = 1250 + 30 * (1 - e)
        sh, pad = _shadow(bw, bh, bh // 2, strength=120)
        base.alpha_composite(sh, (int(W / 2 - bw / 2 - pad), int(y - pad)))
        d = ImageDraw.Draw(base)
        d.rounded_rectangle([W / 2 - bw / 2, y, W / 2 + bw / 2, y + bh], radius=bh // 2, fill=ACC + (a,))
        cta = T["cta_text"]; cf = font(True, 34)
        while tracked_width(d, cta, cf, 3) > bw - 60 and cf.size > 22: cf = font(True, cf.size - 2)
        text_tracked(d, W / 2, y + 30 + (34 - cf.size) * 0.6, cta, cf, INK + (a,), track=3)


# ------------------------------------------------------------------ backgrounds
def vignette(W, H, top=None, bottom=None):
    """Dark gradient at the top and bottom so white text always reads well."""
    top = T["vignette_top"] if top is None else top
    bottom = T["vignette_bottom"] if bottom is None else bottom
    g = Image.new("L", (1, H), 0)
    for y in range(H):
        u = y / H
        v = max(0.0, 1 - u / 0.28) ** 1.6 * top + max(0.0, (u - 0.62) / 0.38) ** 1.6 * bottom
        g.putpixel((0, y), int(min(v, 255)))
    a = g.resize((W, H))
    img = Image.new("RGBA", (W, H), INK + (0,)); img.putalpha(a)
    return img
