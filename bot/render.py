"""Satellite quiz reel: GTA-style camera flights over satellite layers, timer, reveal zoom-out, voice + sound.

Per round:  title  ->  fly-in (fast zoom + swirl)  ->  slow drift with timer  ->  "lock in your guess"
            ->  whip zoom-out + flash  ->  continent view (Blue Marble) with borders, pin, 200 km circle  ->  answer card
"""
import math, subprocess, wave, zlib
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFilter
from .config import W, H, FPS, RADIUS_KM, REVEAL_KM
from .imagery import merc, ground_to_merc, OCEAN
from . import design as D

SR = 44100

# ------------------------------------------------------------------ timeline (minimums; voice can extend them)
MIN_INTRO = 1.0
FLY_IN = 1.2                 # zoom out from the close-up to the quiz view: 2 quick jumps (GTA switch style)
MIN_PAN = 5                  # timer while the camera drifts (whole seconds)
MIN_GUESS = 3                # "lock in your guess" timer
WHIP = 0.55                  # fast zoom-out before the cut
FLASH = 0.18
ZOOM_OUT = 1.5               # continent zoom-out after the cut: 3 quick jumps
MIN_CARD_HOLD = 4.6
AFTER_SPEECH = 1.0
MIN_END = 6.0
GAP = 0.35


def clamp01(t): return min(max(t, 0.0), 1.0)
def ease_io(t): t = clamp01(t); return 0.5 - 0.5 * math.cos(math.pi * t)
def ease_out(t): return 1 - (1 - clamp01(t)) ** 3
def ease_in(t): return clamp01(t) ** 3
def lerp(a, b, k): return a + (b - a) * k
def loglerp(a, b, k): return math.exp(lerp(math.log(a), math.log(b), k))
def stepped(u, n, jump=0.6):
    """Progress 0..1 made of n quick jumps with short holds in between (like the GTA character-switch camera)."""
    u = clamp01(u); i = min(int(u * n), n - 1); f = u * n - i
    return (i + ease_io(min(f / jump, 1.0))) / n
def bounce_drop(t):
    t = clamp01(t)
    return 1 - (1 - t) ** 2 * abs(math.cos(t * math.pi * 1.5))


# ------------------------------------------------------------------ camera over layers
class Sampler:
    """Renders a rotated, zoomed camera view from Mercator layers (mip-mapped, cross-faded at coverage edges)."""

    def __init__(self, layers):
        self.layers = sorted(layers, key=lambda L: L.px)
        self.mips = []
        for L in self.layers:
            lv = [L.img]
            while min(lv[-1].shape[:2]) > 300:
                lv.append(cv2.pyrDown(lv[-1]))
            self.mips.append(lv)

    @staticmethod
    def footprint(cx, cy, width, angle):
        a = math.radians(angle); hw, hh = width / 2, width * H / W / 2
        ex = hw * abs(math.cos(a)) + hh * abs(math.sin(a)); ey = hw * abs(math.sin(a)) + hh * abs(math.cos(a))
        return cx - ex, cy - ey, cx + ex, cy + ey

    def _warp(self, i, cx, cy, width, angle):
        L = self.layers[i]; op = width / W
        k = 0
        while k + 1 < len(self.mips[i]) and op / (L.px * 2 ** (k + 1)) >= 1.0:
            k += 1
        img = self.mips[i][k]; pk = L.px * (L.w / img.shape[1])
        s = op / pk; a = math.radians(angle); ca, sa = math.cos(a), math.sin(a)
        c0 = (cx - L.x0) / pk; r0 = (L.y1 - cy) / pk
        M = np.array([[s * ca, s * sa, c0 - s * (ca * W / 2 + sa * H / 2)],
                      [-s * sa, s * ca, r0 - s * (-sa * W / 2 + ca * H / 2)]], np.float32)
        return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=OCEAN)

    def view(self, cx, cy, width, angle=0.0):
        box = self.footprint(cx, cy, width, angle)
        cont = [i for i, L in enumerate(self.layers) if L.contains(*box, margin=1)]
        if not cont:                                   # outside every layer: use the biggest one
            return self._warp(len(self.layers) - 1, cx, cy, width, angle)
        i = cont[0]; L = self.layers[i]
        img = self._warp(i, cx, cy, width, angle)
        nxt = cont[1] if len(cont) > 1 else None
        if nxt is not None:                            # fade to the next layer before this one runs out
            bw, bh = box[2] - box[0], box[3] - box[1]
            room = min((box[0] - L.x0) / bw, (L.x1 - box[2]) / bw, (box[1] - L.y0) / bh, (L.y1 - box[3]) / bh)
            k = clamp01((0.12 - room) / 0.12)
            if k > 0:
                img = cv2.addWeighted(img, 1 - k, self._warp(nxt, cx, cy, width, angle), k, 0)
        return img


# ------------------------------------------------------------------ the reel
class Reel:
    END_LINE = "So, how many did you get?"

    def __init__(self, rounds, borders=None, tts=None):
        self.rounds = rounds; self.borders = borders; self.tts = tts
        for R in rounds:
            lon, lat = R["lonlat"]
            R["c"] = merc(lon, lat)
            R["E"] = ground_to_merc(R["quiz_km"], lat)                 # quiz view width in Mercator metres
            R["s2"] = Sampler(R["detail_layers"]); R["bm"] = Sampler(R["reveal_layers"])
            km_per_px = R["quiz_km"] / W                                # scale bar: one fixed length per round
            R["scale_km"] = min([1, 2, 5, 10, 20, 50], key=lambda n: abs(n / km_per_px - 200))
        self.clips = []
        self._vig = D.vignette(W, H)
        self._plan()

    # ---- voice first, then the timeline is fitted around it
    def _speak(self, text):
        if not text or not self.tts: return None
        a = self.tts(text, None)
        return a if a is not None and len(a) > 0 else None

    def _plan(self):
        dur = lambda a: len(a) / SR if a is not None else 0.0
        t0 = 0.0; self.starts = []
        for R in self.rounds:
            L = {k: self._speak(R["lines"].get(k)) for k in ("intro", "pan1", "guess", "reveal")}
            v, tl = [], {}
            if L["intro"] is not None: v.append((0.25, L["intro"]))
            tl["intro"] = max(MIN_INTRO, 0.25 + dur(L["intro"]) + 0.2)
            tl["pan0"] = tl["intro"]
            cur = tl["pan0"] + 0.35
            if L["pan1"] is not None:
                v.append((cur, L["pan1"])); cur += dur(L["pan1"]) + GAP
            tl["pan1"] = tl["pan0"] + max(MIN_PAN, math.ceil(cur - tl["pan0"] + 0.3))
            tl["guess0"] = tl["pan1"]
            g = MIN_GUESS
            if L["guess"] is not None:
                v.append((tl["guess0"] + 0.1, L["guess"])); g = max(g, math.ceil(dur(L["guess"]) + 0.4))
            tl["guess1"] = tl["rev0"] = tl["guess0"] + g
            tl["cut"] = tl["rev0"] + WHIP
            tl["zoom1"] = tl["cut"] + ZOOM_OUT
            tl["pin"] = tl["zoom1"] + 0.05; tl["circle"] = tl["pin"] + 0.3; tl["card"] = tl["pin"] + 0.7
            rev = tl["card"] + 0.6
            if L["reveal"] is not None: v.append((rev, L["reveal"]))
            tl["dur"] = max(rev + MIN_CARD_HOLD, rev + dur(L["reveal"]) + AFTER_SPEECH)
            R["tl"] = tl
            self.starts.append(t0)
            self.clips += [(t0 + t, a) for t, a in v]
            t0 += tl["dur"]
        self.rounds_end = t0
        e = self._speak(self.END_LINE)
        if e is not None: self.clips.append((t0 + 1.15, e))
        self.end_dur = max(MIN_END, 1.15 + dur(e) + 2.5)
        self.total = t0 + self.end_dur
        for (s0, a), nxt in zip(self.clips, self.clips[1:] + [(self.total, None)]):
            assert s0 + len(a) / SR <= nxt[0] + 1e-6, "voice overlap"
        print("  timeline: rounds " + ", ".join(f"{R['tl']['dur']:.1f}s" for R in self.rounds)
              + f", end {self.end_dur:.1f}s, total {self.total:.1f}s")

    # ---- camera path (Mercator centre, width, rotation, which sampler)
    def camera(self, R, t):
        tl, (cx, cy), E = R["tl"], R["c"], R["E"]
        rnd = (zlib.crc32(R["loc_id"].encode()) % 1000) / 1000.0
        sx, sy = (1 if rnd > 0.5 else -1), (1 if rnd * 7 % 1 > 0.5 else -1)
        o0 = (-0.07 * E * sx, 0.05 * E * sy); o1 = (0.07 * E * sx, -0.05 * E * sy)
        a0, a1 = -2.5 * sx, 2.5 * sx
        e0 = min(E, max(E / 3.2, ground_to_merc(7.0, R["lonlat"][1])))   # close-up start (not sharper than ~6 m/px)
        if t < tl["pan0"]:                                            # title: still close-up (= start of the fly-out)
            return cx + o0[0], cy + o0[1], e0, a0 - 14 * sx, "s2"
        if t < tl["pan0"] + FLY_IN:                                   # zoom out in 2 jumps with a swirl
            k = stepped((t - tl["pan0"]) / FLY_IN, 2)
            return cx + o0[0], cy + o0[1], loglerp(e0, E, k), lerp(a0 - 14 * sx, a0, k), "s2"
        if t < tl["rev0"]:                                            # slow drift while the timer runs
            k = ease_io((t - tl["pan0"] - FLY_IN) / max(tl["rev0"] - tl["pan0"] - FLY_IN, 0.1))
            return (cx + lerp(o0[0], o1[0], k), cy + lerp(o0[1], o1[1], k),
                    E * lerp(1.0, 0.9, k), lerp(a0, a1, k), "s2")
        if t < tl["cut"]:                                             # whip zoom-out towards the centre
            k = ease_in((t - tl["rev0"]) / WHIP)
            return (cx + o1[0] * (1 - k), cy + o1[1] * (1 - k), loglerp(E * 0.9, E * 1.55, k), a1 * (1 - k), "s2")
        lat = R["lonlat"][1]
        w0, w1 = ground_to_merc(800, lat), ground_to_merc(REVEAL_KM, lat)
        if t < tl["zoom1"]:                                           # continent zoom-out in 3 jumps
            k = stepped((t - tl["cut"]) / ZOOM_OUT, 3)
            return cx, cy, loglerp(w0, w1, k), 0.0, "bm"
        k = clamp01((t - tl["zoom1"]) / 8.0)
        return cx, cy, w1 * lerp(1.0, 0.96, k), 0.0, "bm"

    def _shot(self, R, t):
        cx, cy, w, a, src = self.camera(R, t)
        w_a, w_b = self.camera(R, t - 0.02)[2], self.camera(R, t + 0.02)[2]
        fast = abs(math.log(w_b / w_a)) > 0.04                      # zooming fast -> motion blur
        if not fast:
            return R[src].view(cx, cy, w, a), (cx, cy, w, a, src)
        acc = np.zeros((H, W, 3), np.float32)                       # motion blur: 5 sub-frames
        subs = (-0.016, -0.008, 0.0, 0.008, 0.016)
        for dt in subs:
            c2 = self.camera(R, t + dt)
            acc += R[c2[4]].view(*c2[:4])
        return (acc / len(subs)).astype(np.uint8), (cx, cy, w, a, src)

    # ---- overlays
    def _scale_bar(self, out, R, w, alpha):
        """Small map scale centred under the timer (helps to judge how big things are)."""
        if alpha <= 0: return
        km_per_px = w / W * math.cos(math.radians(R["lonlat"][1])) / 1000
        km = R["scale_km"]; px = km / km_per_px
        y = D.T["timer_y"] + D.T["timer_radius"] + 44; x0 = W / 2 - px / 2
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        f = D.font(True, 24); lab = f"{km} km"; lw = d.textlength(lab, font=f)
        d.rounded_rectangle([x0 - 20, y - 14, x0 + px + 20, y + 44], radius=18, fill=D.INK + (110,))
        a = 235
        d.line([(x0, y + 26), (x0 + px, y + 26)], fill=(255, 255, 255, a), width=4)
        for xx in (x0, x0 + px):
            d.line([(xx, y + 16), (xx, y + 30)], fill=(255, 255, 255, a), width=4)
        d.text((W / 2 - lw / 2, y - 12), lab, font=f, fill=(255, 255, 255, a))
        if alpha < 1:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        out.alpha_composite(layer)

    def _credit(self, out, y, text, alpha=1.0):
        """Small, half-transparent source line (drawn on its own layer so the transparency is real)."""
        if alpha <= 0: return
        f = D.font(False, 21)
        tw = ImageDraw.Draw(out).textlength(text, font=f)
        asc, desc = f.getmetrics()
        layer = Image.new("RGBA", (int(tw) + 8, asc + desc + 6), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        d.text((3, 3), text, font=f, fill=(0, 0, 0, int(150 * alpha)))
        d.text((2, 2), text, font=f, fill=(255, 255, 255, int(185 * alpha)))
        out.alpha_composite(layer, (int(W / 2 - tw / 2) - 2, int(y) - 2))

    def _borders(self, out, cam, alpha):
        if not self.borders or alpha <= 0: return
        cx, cy, w, a, _ = cam
        op = w / W
        box = Sampler.footprint(cx, cy, w, a)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        col = (255, 255, 255, int(170 * alpha))
        for line in self.borders.visible(*box):
            u = (line[:, 0] - cx) / op + W / 2; v = -(line[:, 1] - cy) / op + H / 2
            pts = list(zip(u.tolist(), v.tolist()))
            if len(pts) > 1:
                d.line(pts, fill=col, width=3, joint="curve")
        out.alpha_composite(layer)

    def round_frame(self, t, ri):
        R = self.rounds[ri]; tl = R["tl"]; n = len(self.rounds)
        self._lat = R["lonlat"][1]
        img, cam = self._shot(R, t)
        out = Image.fromarray(img).convert("RGBA")
        if t < tl["pan0"]:                                            # title screen
            out = Image.fromarray((np.asarray(out.convert("RGB"), np.float32) * D.T["intro_dim"]).astype(np.uint8)).convert("RGBA")
        out.alpha_composite(self._vig)
        if t < tl["pan0"]:
            small, big, sub = R["intro"]
            d = ImageDraw.Draw(out)
            size = D.T["title_size"]; lines = [big]; words = big.split()
            if d.textlength(big, font=D.font(True, size)) > W - 90 and len(words) > 1:   # two big lines
                i = min(range(1, len(words)),
                        key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
                lines = [" ".join(words[:i]), " ".join(words[i:])]
            while max(d.textlength(l, font=D.font(True, size)) for l in lines) > W - 90: size -= 4
            step = size * 1.08
            y_big = D.T["title_y"] - size * 0.62 - step * (len(lines) - 1) / 2
            if D.T["show_edition_chip"]:
                D.glass_pill(out, W / 2, y_big - 120, sub.upper(), D.font(True, 30), fg=D.ACC, track=7, dot=D.ACC)
            for i, line in enumerate(lines):
                D.soft_text(out, W / 2, y_big + i * step, line, D.font(True, size))
            if D.T["show_title_underline"]:
                uy = y_big + step * (len(lines) - 1) + size * 1.42
                ImageDraw.Draw(out).rounded_rectangle([W / 2 - 70, uy, W / 2 + 70, uy + 10], radius=5, fill=D.ACC)
        elif t < tl["rev0"]:                                          # guessing
            if t < tl["pan1"]:
                D.timer(out, W / 2, D.T["timer_y"], tl["pan1"] - t, tl["pan1"] - tl["pan0"], (t - tl["pan0"]) % 1)
                D.glass_pill(out, W / 2, 1540, "SATELLITE VIEW  ·  WHERE IS THIS?", D.font(True, 28), track=3,
                             alpha_mul=clamp01((t - tl["pan0"]) / 0.4))
            else:
                D.timer(out, W / 2, D.T["timer_y"], tl["guess1"] - t, tl["guess1"] - tl["guess0"], (t - tl["guess0"]) % 1)
                D.glass_pill(out, W / 2, 1540, "LOCK IN YOUR GUESS", D.font(True, 36), track=4, dot=D.AMBER)
            self._scale_bar(out, R, cam[2], clamp01((t - tl["pan0"] - FLY_IN) / 0.4))
            info = R.get("info") or {}
            if info.get("source") == "sentinel-2":
                self._credit(out, 1632, f"Contains modified Copernicus Sentinel data {info.get('year', '')}")
            elif info.get("source") == "demo":
                self._credit(out, 1632, "DEMO IMAGERY (offline test)")
        else:                                                         # reveal
            if t >= tl["cut"]:
                self._borders(out, cam, clamp01((t - tl["cut"]) / 0.8))
                cx, cy, w, a, _ = cam
                op = w / W
                d = ImageDraw.Draw(out)
                px, py = W / 2, H / 2                                 # the place is the camera centre
                if t >= tl["circle"]:
                    k = ease_out((t - tl["circle"]) / 0.6)
                    rr = ground_to_merc(RADIUS_KM, self._lat) / op * k
                    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
                    ld.ellipse([px - rr, py - rr, px + rr, py + rr], fill=D.RED + (45,), outline=D.RED + (235,), width=5)
                    out.alpha_composite(layer)
                    if k > 0.95:
                        lab = f"{RADIUS_KM} km"; lf = D.font(True, 26); lw = d.textlength(lab, font=lf)
                        by = py + rr + 12
                        d.rounded_rectangle([px - lw / 2 - 18, by, px + lw / 2 + 18, by + 42], radius=21, fill=D.RED + (240,))
                        d.text((px - lw / 2, by + 3), lab, font=lf, fill=(255, 255, 255))
                if t >= tl["pin"]:
                    land = bounce_drop((t - tl["pin"]) / 0.35)
                    D.pin(d, px, py - 260 * (1 - land), D.RED)
                src = "NASA Blue Marble" if R.get("reveal_source", "bluemarble") == "bluemarble" else "Map"
                self._credit(out, D.T["hud_y"] + 132, f"{src}  ·  Borders: Natural Earth",
                             clamp01((t - tl["cut"]) / 0.6))
            fl = 1 - abs(t - tl["cut"]) / (FLASH / 2)                  # white flash at the cut
            if fl > 0:
                out.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(235 * fl))))
            if t >= tl["card"]:
                lo, la = R["lonlat"]
                fact = R.get("fact") or f"{abs(la):.2f}°{'N' if la >= 0 else 'S'}, {abs(lo):.2f}°{'E' if lo >= 0 else 'W'}"
                D.answer_card(out, W, D.T["card_y"], R["place"], fact, (t - tl["card"]) / 0.45)
        D.round_hud(out, ri + 1, n, W, R.get("difficulty"))
        return out.convert("RGB")

    def end_frame(self, t):
        if not hasattr(self, "_end_bg"):
            last = self.round_frame(self.rounds[-1]["tl"]["dur"] - 0.01, len(self.rounds) - 1)
            self._end_bg = last.filter(ImageFilter.GaussianBlur(34)).convert("RGBA")
        img = self._end_bg.copy()
        D.end_card(img, W, H, t, len(self.rounds))
        return img.convert("RGB")

    def frame(self, abs_t):
        if abs_t >= self.rounds_end:
            return self.end_frame(abs_t - self.rounds_end)
        ri = max(i for i, s0 in enumerate(self.starts) if s0 <= abs_t)
        return self.round_frame(abs_t - self.starts[ri], ri)

    def key_frames(self):
        """Typical screens for the one-time OpenAI design review."""
        R = self.rounds[0]; tl = R["tl"]; s0 = self.starts[0]
        ts = [s0 + 0.3, s0 + tl["pan0"] + 2.2, s0 + tl["guess0"] + 1.0, s0 + tl["card"] + 1.6, self.rounds_end + 2.2]
        return [self.frame(t) for t in ts]

    # ---- audio
    def audio(self, path):
        N = int(self.total * SR); a = np.zeros(N, np.float32)
        def add(t0, sig, gain=1.0):
            i = int(t0 * SR); j = min(N, i + len(sig))
            if j > i: a[i:j] += gain * sig[: j - i]
        tt = lambda dur: np.arange(int(dur * SR)) / SR
        def tick(hi=False):
            x = tt(0.06); return 0.3 * np.sin(2 * np.pi * (1800 if hi else 1300) * x) * np.exp(-x * 70)
        def pop():
            x = tt(0.15); f = 500 + 900 * np.exp(-x * 30)
            return 0.4 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 25)
        def whoosh(d=0.5, g=0.45):
            x = tt(d); nn = np.convolve(np.random.default_rng(1).normal(size=len(x)), np.ones(30) / 30, "same")
            return g * nn * np.sin(np.pi * x / d) ** 2
        def boom():
            x = tt(0.7); return 0.5 * np.sin(2 * np.pi * (60 + 40 * np.exp(-x * 8)) * x) * np.exp(-x * 6)
        def chime():
            out = np.zeros(int(0.9 * SR))
            for k, f in enumerate([660, 880, 1320]):
                x = tt(0.6); s = 0.22 * np.sin(2 * np.pi * f * x) * np.exp(-x * 5)
                i = int(k * 0.1 * SR); out[i:i + len(s)] += s
            return out
        speech = [(t0, t0 + len(c) / SR) for t0, c in self.clips]
        def free(t0, d): return all(t0 + d < s0 - 0.08 or t0 > s1 + 0.08 for s0, s1 in speech)
        def sfx(t0, sig, gain=1.0):
            if free(t0, len(sig) / SR): add(t0, sig, gain)
        for r, R in enumerate(self.rounds):
            b = self.starts[r]; tl = R["tl"]
            pan_s, guess_s = round(tl["pan1"] - tl["pan0"]), round(tl["guess1"] - tl["guess0"])
            sfx(b, pop())
            for j in range(2):                                         # one whoosh per camera jump
                add(b + tl["pan0"] + j * FLY_IN / 2, whoosh(0.45, 0.42 if j == 0 else 0.3))
            for k in range(1, pan_s): sfx(b + tl["pan0"] + k, tick(k >= pan_s - 2), 0.8)
            for k in range(guess_s): sfx(b + tl["guess0"] + k, tick(k >= guess_s - 1), 0.8)
            add(b + tl["rev0"], whoosh(WHIP + 0.1, 0.55)); add(b + tl["cut"], boom(), 0.9)
            for j in range(1, 3):
                add(b + tl["cut"] + j * ZOOM_OUT / 3, whoosh(0.4, 0.32))
            sfx(b + tl["pin"] + 0.2, pop()); sfx(b + tl["card"], chime())
        sfx(self.rounds_end + 0.15, chime())
        from .audiofx import fade, polish_voice, master
        voice = np.zeros(N, np.float32)
        for t0, clip in self.clips:
            clip = fade(clip); i = int(t0 * SR); j = min(N, i + len(clip)); voice[i:j] += clip[: j - i]
        a = master(a + polish_voice(voice) * 1.1)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((a * 32000).astype(np.int16).tobytes())

    def render(self, out_mp4, workdir):
        wav = f"{workdir}/audio.wav"; self.audio(wav)
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav,
                              "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-shortest", "-movflags", "+faststart",
                              str(out_mp4)], stdin=subprocess.PIPE)
        nf = int(self.total * FPS)
        for fi in range(nf):
            p.stdin.write(np.asarray(self.frame(fi / FPS), np.uint8).tobytes())
            if fi % (FPS * 5) == 0: print(f"  frame {fi}/{nf}", flush=True)
        p.stdin.close()
        if p.wait() != 0: raise RuntimeError("ffmpeg failed")
