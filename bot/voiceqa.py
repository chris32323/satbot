"""Voice quality check for each spoken line.

1) Pitch analysis (free, no AI): tracks the voice pitch (F0) and flags
   - a voice that is too high for an adult man (sounds like a teenager),
   - voice cracks: sudden big pitch jumps,
   - wobbly / unstable pitch (strong up-and-down swings).
2) Optional AI listening test (gpt-audio-1.5 hears the line, ~0.1 ct per line):
   catches what numbers miss (strange mood swings, artifacts, odd pronunciation).
Bad takes are generated again; the best take is kept.
"""
import base64, io, json, os, re, wave
import numpy as np
import requests

MAX_PITCH_HZ = float(os.getenv("VOICE_MAX_PITCH_HZ") or 0)      # 0 = off; e.g. 175 = reject a too high male voice
MAX_JUMPS = int(os.getenv("VOICE_MAX_JUMPS") or 1)              # allowed sudden pitch jumps per line
MAX_SWING = float(os.getenv("VOICE_MAX_SWING") or 13)           # semitones between low (5%) and high (95%) pitch
TRIES = int(os.getenv("VOICE_QA_TRIES") or 2)                   # max takes per line (each take costs credits)
AI_CHECK = (os.getenv("VOICE_AI_CHECK") or "0") == "1"   # off unless VOICE_AI_CHECK=1
AI_MODEL = os.getenv("VOICE_AI_MODEL") or "gpt-audio-1.5"


# ------------------------------------------------------------------ pitch tracking (YIN)
def pitch_track(a, sr):
    """Returns F0 per 10 ms frame in Hz (0 = silence / unvoiced)."""
    if sr != 16000:
        x = np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32); sr = 16000
    else:
        x = a.astype(np.float32)
    fl, hop = int(0.04 * sr), int(0.01 * sr)
    tmax, tmin = int(sr / 60), int(sr / 400)
    if len(x) < fl + tmax + hop:
        return np.zeros(0)
    gate = 0.25 * np.sqrt(np.mean(x ** 2)) + 1e-6
    lags = np.arange(1, tmax + 1)
    f0 = []
    for i in range(0, len(x) - fl - tmax, hop):
        fr = x[i:i + fl]
        if np.sqrt(np.mean(fr ** 2)) < gate:
            f0.append(0.0); continue
        seg = x[i:i + fl + tmax]
        sh = np.lib.stride_tricks.sliding_window_view(seg, fl)[1:tmax + 1]       # (tmax, fl)
        d = np.sum((sh - fr) ** 2, axis=1)
        cm = d * lags / np.maximum(np.cumsum(d), 1e-9)                          # cumulative mean normalised
        cand = np.where(cm[tmin - 1:] < 0.2)[0]
        if len(cand) == 0:
            f0.append(0.0); continue
        t = cand[0] + tmin - 1
        while t + 1 < len(cm) and cm[t + 1] < cm[t]:
            t += 1
        f0.append(sr / (t + 1))
    return np.array(f0)


def analyse(a, sr=44100):
    """Pitch report for one take. Lower score = better."""
    f0 = pitch_track(a, sr)
    v = f0[f0 > 0]
    if len(v) < 10:
        return dict(ok=True, score=0.0, median=0, jumps=0, swing=0, note="too short to judge")
    # median filter over voiced frames (removes single-frame tracker errors)
    k = 5; pad = np.pad(v, (k // 2, k // 2), mode="edge")
    sm = np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)
    st = 12 * np.log2(sm / np.median(sm))
    jumps = int(np.sum(np.abs(np.diff(st)) > 5))                              # >5 semitones in 10 ms = crack
    swing = float(np.percentile(st, 95) - np.percentile(st, 5))
    med = float(np.median(sm))
    problems = []
    if MAX_PITCH_HZ and med > MAX_PITCH_HZ: problems.append(f"too high {med:.0f} Hz")
    if jumps > MAX_JUMPS: problems.append(f"{jumps} voice cracks")
    if swing > MAX_SWING: problems.append(f"unstable pitch ({swing:.1f} semitones)")
    score = (max(0, med - MAX_PITCH_HZ) / 10 if MAX_PITCH_HZ else 0) + 2 * max(0, jumps - MAX_JUMPS) + max(0, swing - MAX_SWING)
    return dict(ok=not problems, score=round(score, 2), median=round(med), jumps=jumps, swing=round(swing, 1),
                note=", ".join(problems) or "ok")


# ------------------------------------------------------------------ AI listening test
def _wav_b64(a, sr=44100):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32000).astype(np.int16).tobytes())
    return base64.b64encode(b.getvalue()).decode()


def ai_listen(a, text, sr=44100):
    """gpt-audio-1.5 listens to the take. Returns (ok, problem) or None if the check is unavailable."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not AI_CHECK or not key:
        return None
    prompt = ("You are a strict audio quality checker for a voice-over by an ADULT male narrator (about 30 years old). "
              f'The intended text is: "{text}". Listen to the recording. Fail it if ANY of these is audible: '
              "voice cracks or breaks, sudden pitch jumps, sounds like a teenage boy, wobbly or unstable pitch, "
              "strange mood swings within the line, robotic or metallic artifacts, clicks, mumbled or wrong words. "
              'Reply ONLY with JSON: {"ok": true or false, "problem": "short reason or empty"}')
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", timeout=90,
                          headers={"Authorization": f"Bearer {key}"},
                          json=dict(model=AI_MODEL, modalities=["text"], messages=[{"role": "user", "content": [
                              {"type": "text", "text": prompt},
                              {"type": "input_audio", "input_audio": {"data": _wav_b64(a, sr), "format": "wav"}}]}]))
        if r.status_code >= 400:
            print(f"  voice QA: AI check unavailable (HTTP {r.status_code})")
            return None
        txt = r.json()["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        j = json.loads(m.group(0)) if m else {}
        return bool(j.get("ok", True)), str(j.get("problem", ""))[:120]
    except Exception as e:
        print(f"  voice QA: AI check skipped ({type(e).__name__})")
        return None


def best_take(make, text, label=""):
    """Calls make() up to TRIES times until a take passes both checks. Returns the best take."""
    takes = []
    for n in range(1, TRIES + 1):
        a = make()
        rep = analyse(a)
        verdict = ai_listen(a, text) if rep["ok"] else None
        if verdict is not None and not verdict[0]:
            rep["ok"] = False; rep["score"] += 3; rep["note"] = "AI: " + (verdict[1] or "sounds off")
        takes.append((rep["score"], n, a, rep))
        if rep["ok"]:
            break
        print(f"  voice QA: take {n} rejected ({rep['note']}) for: {label or text}")
    score, n, a, rep = min(takes, key=lambda t: (t[0], t[1]))
    ai = "" if not AI_CHECK else " + AI"
    print(f"  voice QA: '{label or text}' -> take {n}/{len(takes)}, {rep['median']} Hz, "
          f"cracks {rep['jumps']}, swing {rep['swing']} st ({'ok' if rep['ok'] else 'best available'}{ai})")
    return a
