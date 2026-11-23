"""Entry point of the satellite quiz bot ("Where on Earth?").

  python -m bot.main render  --out out                       # pick places, fetch satellite imagery, voice, render
  python -m bot.main render  --out out --demo --fake-voice   # offline test (procedural imagery, beep voice)
  python -m bot.main publish --out out --video-url URL
  python -m bot.main voices  --out out                       # one-time: record every line with ElevenLabs
  python -m bot.main design  --out out                       # one-time: OpenAI design review
  python -m bot.main refresh-token                           # prints a refreshed Instagram token
"""
import argparse, json, os, random, zlib, datetime as dt
from pathlib import Path
import numpy as np
from . import config

BOT_VERSION = "2026-11-21 sat-1 (first version)"
HISTORY = config.DATA / "history.json"
LOCATIONS = config.DATA / "locations.json"

SAY_INTRO = ["Where on Earth is this?", "Next round. Where is this?", "Last round. Where is this?"]
SAY_PAN = "Can you guess it?"          # when the camera starts moving
SAY_GUESS = "Lock in your guess."      # last seconds of the timer
SAY_RIGHT = "Did you get it right?"    # after the first reveal

# round 1: famous places (big cities, natural wonders), easy/medium first.
# round 2: the rest - islands, landscapes, human-made patterns - medium/hard first.
ROUND1_WEIGHTS = {"city": 0.5, "wonder": 0.5}
ROUND2_WEIGHTS = {"island": 0.3, "nature": 0.4, "pattern": 0.3}
PAUSE_DAYS = 21               # a place without usable imagery is skipped for 3 weeks, then tried again
RECENT_POSTS = 45             # a place is not shown again within the last 45 reels (if possible)


def intro_for(slot, n):
    if slot == 0:
        return ("ROUND 1", config.TITLE, config.TITLE_EDITION)
    if n >= 3 and slot == n - 1:
        return ("FINAL ROUND", "LAST CHANCE!", "Only the pros get this one")
    return (f"ROUND {slot + 1}", "WHERE IS THIS?", "It gets harder now")


def reveal_line(loc):
    """'And this is Venice, Italy.' - what the voice says at the reveal."""
    r = (loc.get("reveal") or "").strip()
    return r or f"And this is {loc.get('say') or loc['name']}."


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _paused(history):
    """{location_id: 'YYYY-MM-DD'} of places that had no usable imagery."""
    nc = history.get("no_coverage")
    if not isinstance(nc, dict):
        nc = {}; history["no_coverage"] = nc
    return nc


def blocked(history):
    limit = (dt.date.today() - dt.timedelta(days=PAUSE_DAYS)).isoformat()
    return {i for i, d in _paused(history).items() if d > limit}


def available_locations(history):
    """(fresh, reuse): fresh = not shown in the last RECENT_POSTS reels; reuse = shown before, longest ago first."""
    locs = load_json(LOCATIONS, [])
    dead = blocked(history)
    last_seen, recent = {}, set()
    posts = history.get("posted", [])
    for i, post in enumerate(posts):
        for lid in post.get("location_ids", []):
            last_seen[lid] = i
    for post in posts[-RECENT_POSTS:]:
        recent.update(post.get("location_ids", []))
    alive = [l for l in locs if l["id"] not in dead]
    fresh = [l for l in alive if l["id"] not in recent]
    reuse = sorted((l for l in alive if l["id"] in recent), key=lambda l: last_seen.get(l["id"], -1))
    return fresh, reuse


def weighted_order(rng, locs, weights, rank=None):
    """Random order where the TYPE is drawn by its weight first, then a random place of that type."""
    groups = {}
    for l in locs:
        if weights.get(l.get("type"), 0) > 0:
            groups.setdefault(l["type"], []).append(l)
    for g in groups.values():
        rng.shuffle(g)
        if rank:                                     # within a type: preferred difficulty first (popped from the end)
            g.sort(key=rank, reverse=True)
    out = []
    while groups:
        types = sorted(groups)
        t = rng.choices(types, [weights[x] for x in types])[0]
        out.append(groups[t].pop())
        if not groups[t]:
            del groups[t]
    return out


def candidates_for(slot, rng, pool):
    if slot == 0:
        return (weighted_order(rng, pool, ROUND1_WEIGHTS, rank=lambda l: l.get("difficulty") == "hard")
                + weighted_order(rng, pool, ROUND2_WEIGHTS, rank=lambda l: l.get("difficulty") == "hard"))
    return (weighted_order(rng, pool, ROUND2_WEIGHTS, rank=lambda l: l.get("difficulty") == "easy")
            + weighted_order(rng, pool, ROUND1_WEIGHTS, rank=lambda l: l.get("difficulty") == "easy"))


def _ai_check(place):
    """OpenAI image check for one candidate day (None = no check available)."""
    from . import vision
    if not vision.ENABLED:
        return None

    def check(preview):
        res = vision.check(preview, place)
        if res is None:
            return True                                   # check not available -> do not block
        print(f"  AI check: usable={res['usable']} interesting={res['interesting']} ({res['reason']})")
        return res["usable"]
    return check


def get_imagery(loc, demo=False, log=print):
    """(detail_layers, reveal_layers, info, reveal_source) for one place. Raises imagery.ImageryError."""
    from . import imagery
    lon, lat, km = loc["lon"], loc["lat"], float(loc["km"])
    if demo:
        detail, reveal, info = imagery.demo_layers(lon, lat, km, config.REVEAL_KM, seed=zlib.crc32(loc["id"].encode()) % 1000)
        return detail, reveal, info, "demo"
    detail, info = imagery.sentinel_layers(lon, lat, km, log=log, bright=bool(loc.get("bright")),
                                           check=_ai_check(f"{loc['name']}, {loc['country']}"))
    try:
        reveal, src = imagery.reveal_layers(lon, lat, config.REVEAL_KM), "bluemarble"
    except (imagery.ImageryError, imagery.ImageryTrouble) as e:
        log(f"  Blue Marble unavailable ({e}) - using the drawn Natural Earth map for the zoom-out")
        from .borders import land_layers
        try:
            reveal, src = land_layers(lon, lat, config.REVEAL_KM), "naturalearth"
        except Exception as e2:
            raise imagery.ImageryTrouble(f"no map for the zoom-out ({e2})")
    return detail, reveal, info, src


def build_rounds(n, rng, history, demo=False):
    from .imagery import ImageryError, ImageryTrouble
    rounds = []; fresh, reuse = available_locations(history); tried = set(); countries = set(); trouble = []
    for slot in range(n):
        # 1) places not shown recently, 2) places shown the longest time ago, 3) anything
        order = candidates_for(slot, rng, fresh) + candidates_for(slot, rng, reuse[:max(len(reuse) // 2, 10)])
        order += weighted_order(rng, fresh + reuse, {t: 1 for t in ("city", "wonder", "island", "nature", "pattern")})
        for loc in order:
            if loc["id"] in tried or loc["country"] in countries:      # never two places of one country in a reel
                continue
            tried.add(loc["id"])
            print(f"round {slot + 1}: trying {loc['name']}, {loc['country']} ({loc['type']}, {loc['difficulty']}, "
                  f"{loc['km']} km)")
            try:
                detail, reveal, info, src = get_imagery(loc, demo=demo)
            except ImageryError as e:
                print(f"  {e} - skipping this place for {PAUSE_DAYS} days")
                _paused(history)[loc["id"]] = dt.date.today().isoformat(); continue
            except ImageryTrouble as e:                   # service problem: try another place, do not pause
                trouble.append(str(e)); print(f"  problem: {e} - trying another place (not paused)")
                if len(trouble) >= 3:
                    raise RuntimeError("the satellite imagery services do not work right now (3 places in a row). "
                                       f"Last error: {trouble[-1]}")
                continue
            reveal_text = reveal_line(loc)
            if slot == 0:
                reveal_text += " " + SAY_RIGHT
            place = loc.get("display") or (loc["name"] if loc["country"] in loc["name"]
                                           else f"{loc['name']}, {loc['country']}")
            rounds.append(dict(
                loc_id=loc["id"], lonlat=(loc["lon"], loc["lat"]), quiz_km=float(loc["km"]),
                detail_layers=detail, reveal_layers=reveal, info=info, reveal_source=src,
                difficulty=loc.get("difficulty", "medium"), place=place, fact=(loc.get("fact") or "").strip(),
                intro=intro_for(slot, n),
                lines=dict(intro=SAY_INTRO[min(slot, 2)], pan1=SAY_PAN, guess=SAY_GUESS, reveal=reveal_text)))
            countries.add(loc["country"]); trouble.clear()
            print(f"  ok: {info['source']} {info.get('date', '')} ({info.get('provider', '')})")
            break
        else:
            raise RuntimeError("ran out of places with usable satellite imagery")
    return rounds


def make_caption(rounds):
    years = sorted({r["info"].get("year") for r in rounds if r["info"].get("source") == "sentinel-2"} - {None})
    via = sorted({"Microsoft Planetary Computer" if r["info"].get("provider") == "planetary-computer"
                  else "AWS Open Data / Earth Search" for r in rounds if r["info"].get("source") == "sentinel-2"})
    s2 = (f"Satellite imagery: Contains modified Copernicus Sentinel data {', '.join(years)} "
          f"(Sentinel-2, via {' and '.join(via)}).\n" if years else "")
    bm = ("Zoom-out: NASA Blue Marble (NASA Earth Observatory / GIBS). "
          if any(r.get("reveal_source") == "bluemarble" for r in rounds) else "")
    return (f"Where on Earth is this? 🛰️ {len(rounds)} rounds – guess before the timer runs out!\n\n"
            f"How many did you get right? Comment your score 👇 New round every day.\n\n"
            f"{s2}{bm}Borders: Natural Earth.\n\n{config.HASHTAGS}")


def _tts(args, out):
    if args.fake_voice:
        from .render import SR
        return lambda text, mx: (0.15 * np.sin(2 * np.pi * 180 * np.arange(int(len(text) * 0.065 * SR)) / SR)
                                 ).astype(np.float32)
    try:
        from .voice import Voice
        return Voice(out / "voice").say
    except Exception as e:
        if config.REQUIRE_VOICE and not args.demo:
            raise
        print(f"WARNING: voice disabled ({e})")
        return None


def cmd_render(args):
    print(f"satbot version {BOT_VERSION}")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else int(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    rng = random.Random(seed)
    history = load_json(HISTORY, {"posted": [], "no_coverage": {}})
    try:
        rounds = build_rounds(config.ROUNDS_PER_REEL, rng, history, demo=args.demo)
    finally:
        HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")  # paused places
    tts = _tts(args, out)
    from .borders import Borders
    from .render import Reel
    reel = Reel(rounds, borders=Borders(), tts=tts)
    print("rendering video ..."); reel.render(out / "reel.mp4", str(out))
    for i, f in enumerate(reel.key_frames()):                         # preview stills (uploaded as artifact)
        f.convert("RGB").resize((540, 960)).save(out / f"preview_{i + 1}.jpg", quality=86)
    (out / "caption.txt").write_text(make_caption(rounds), encoding="utf-8")
    meta = dict(date=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), seed=seed,
                location_ids=[r["loc_id"] for r in rounds], places=[r["place"] for r in rounds],
                imagery=[r["info"] for r in rounds])
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print("done:", out / "reel.mp4")


def cmd_publish(args):
    from .instagram import publish_reel
    out = Path(args.out)
    caption = (out / "caption.txt").read_text(encoding="utf-8")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    mid, link = publish_reel(args.video_url, caption)
    print("published:", mid, link or "")
    history = load_json(HISTORY, {"posted": [], "no_coverage": {}})
    history["posted"].append(dict(date=meta["date"], media_id=mid, permalink=link,
                                  location_ids=meta["location_ids"], places=meta["places"]))
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_refresh(_):
    from .instagram import refresh_token
    tok, exp = refresh_token()
    print(tok)


def cmd_design(args):
    """One-time: let OpenAI review real frames and improve the design values (saved to data/design.json)."""
    print(f"satbot version {BOT_VERSION}")
    rng = random.Random(int(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")) + 7)
    history = load_json(HISTORY, {"posted": [], "no_coverage": {}})     # read only, not saved
    rounds = build_rounds(config.ROUNDS_PER_REEL, rng, history, demo=args.demo)
    from .borders import Borders
    from . import designer
    designer.run(rounds, Borders(), args.out)


def all_lines():
    """Every sentence the bot can ever say: fixed lines first (they are in every reel), then the reveals."""
    from .render import Reel
    locs = load_json(LOCATIONS, [])
    reveals = [reveal_line(l) for l in locs]
    random.Random(7).shuffle(reveals)
    return SAY_INTRO + [SAY_PAN, SAY_GUESS, SAY_RIGHT, Reel.END_LINE] + reveals


def cmd_voices(args):
    """One-time: record every sentence into data/voice/ (after that the voice costs nothing)."""
    print(f"satbot version {BOT_VERSION}")
    from .voice import Voice, prune_cache, sentences
    v = Voice(Path(args.out) / "voice")
    budget = int(os.getenv("ELEVEN_BUDGET") or 6000)          # max ElevenLabs credits for this run
    texts = all_lines(); missing = 0
    for text in texts:
        todo = sum(len(s) for s in sentences(text) if not Voice.cache_path(s).exists())
        if todo and v.chars + todo > budget:
            missing += 1; continue                            # over budget: leave it for later
        v.say(text)
    removed = prune_cache(texts)
    print(f"voice cache ready (ElevenLabs): {v.hits} already there, {v.misses} new (~{v.chars} credits), "
          f"{removed} old recordings deleted. Commit happens in the next step.")
    if missing:
        print(f"budget of {budget} credits reached: {missing} lines not recorded yet - the daily run records a "
              f"missing line automatically when it needs it (~40 credits), or run prepare_voices again later.")


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render"); r.add_argument("--out", default="out"); r.add_argument("--demo", action="store_true")
    r.add_argument("--fake-voice", action="store_true"); r.add_argument("--seed", type=int)
    p = sub.add_parser("publish"); p.add_argument("--out", default="out"); p.add_argument("--video-url", required=True)
    sub.add_parser("refresh-token")
    vo = sub.add_parser("voices"); vo.add_argument("--out", default="out")
    de = sub.add_parser("design"); de.add_argument("--out", default="out"); de.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)
    {"render": cmd_render, "publish": cmd_publish, "refresh-token": cmd_refresh, "voices": cmd_voices,
     "design": cmd_design}[a.cmd](a)


if __name__ == "__main__":
    main()
