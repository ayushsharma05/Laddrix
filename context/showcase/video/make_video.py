# ruff: noqa: E501  (the scene list and the HTML/CSS templates read best one item per line)
"""Builds the Laddrix advert (#455): about three minutes of the posters, the real screens and
title cards, with music.

    node context/showcase/render.cjs              # the posters first, if they changed
    python3 context/showcase/video/make_video.py

1. draws the title cards and the framed screenshots (HTML pages, photographed by Playwright);
2. films each scene with a slow camera move: a zoom, or a glide from one part of the picture
   to another (ffmpeg zoompan), and joins the scenes with transitions (xfade);
3. writes the soundtrack from plain sine waves and noise (numpy), so there is nothing to
   license: a chord pad, then bass, a beat and an arpeggio as the advert builds, a whoosh on
   every cut, and a calm ending;
4. puts the two together in context/showcase/video/laddrix-showcase.mp4.
"""

import html
import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SHOWCASE = HERE.parent
POSTERS = SHOWCASE.parent / "images" / "showcase"
SHOTS = SHOWCASE.parent / "images" / "showcase" / "screens"  # context/scripts/shoot-showcase.cjs
OUT = HERE / "laddrix-showcase.mp4"
FPS = 30
FADE = 0.8  # seconds each transition takes
RATE = 44100


@dataclass
class Scene:
    """One picture on screen. The camera goes from `start` to `end`, each (x, y, zoom), where
    x and y are the centre as a share of the picture's width and height."""

    picture: (
        str  # a poster name, "shot:<file>|<kicker>|<headline>", or "card:<kicker>|<headline>|<sub>"
    )
    seconds: float
    start: tuple[float, float, float] = (0.5, 0.5, 1.0)
    end: tuple[float, float, float] = (0.5, 0.5, 1.06)
    act: int = 1  # which part of the music plays under it


C = (0.5, 0.5)
SCENES = [
    # 1. the hook
    Scene("card:|Trading, the way professionals do it.|", 3.5, (*C, 1.0), (*C, 1.05), 1),
    Scene("card:NSE · BSE|Live prices. Instant orders. Real risk control.|", 4.0, act=1),
    Scene("card:|Introducing|", 2.5, (*C, 1.0), (*C, 1.08), 1),
    Scene("outro", 5.0, (*C, 1.12), (*C, 1.0), 1),
    # 2. the screens
    Scene("card:PART ONE|Meet the terminal|Everything a trader needs, on one screen.", 3.5, act=2),
    Scene(
        "shot:trading.png|THE TRADING PAGE|Watch, ladders, orders, positions, audit: live",
        5.5,
        (0.5, 0.52, 1.0),
        (0.5, 0.52, 1.12),
        2,
    ),
    Scene(
        "shot:ladder.png|THE PRICE LADDER|Click a price. Trade it. See the queue.",
        5.0,
        (0.5, 0.55, 1.05),
        (0.42, 0.6, 1.35),
        2,
    ),
    Scene(
        "shot:ticket.png|THE ORDER TICKET|It knows the rules: the server tells it what is allowed",
        5.0,
        act=2,
    ),
    Scene(
        "shot:chain.png|OPTION CHAIN|Every strike, every Greek, live",
        5.0,
        (0.5, 0.55, 1.0),
        (0.5, 0.6, 1.18),
        2,
    ),
    Scene(
        "shot:strategy.png|STRATEGY BUILDER|Build multi-leg option strategies in seconds",
        5.0,
        act=2,
    ),
    Scene(
        "shot:depth.png|MARKET DEPTH|Every level of the book, live",
        5.0,
        (0.5, 0.55, 1.0),
        (0.55, 0.5, 1.15),
        2,
    ),
    Scene(
        "shot:autospreader.png|AUTOSPREADER|Quote one leg, hedge the rest the moment it fills",
        5.5,
        (0.5, 0.55, 1.0),
        (0.5, 0.45, 1.15),
        2,
    ),
    Scene(
        "shot:spread-chart.png|SPREAD CHARTS|The price of a whole spread, as candles", 4.5, act=2
    ),
    Scene("shot:structures.png|STRUCTURES|Spreads you hold, with their own P&L", 4.5, act=2),
    Scene(
        "shot:algos.png|ALGOS|Slicer · iceberg · POV · TWAP · VWAP · protect",
        5.0,
        (0.5, 0.55, 1.0),
        (0.45, 0.5, 1.15),
        2,
    ),
    # 3. under the hood
    Scene("card:PART TWO|Under the hood|Engineered like the systems big desks run on.", 3.5, act=3),
    Scene("02-architecture", 5.0, (*C, 1.0), (*C, 1.04), 3),
    Scene("02-architecture", 6.0, (0.38, 0.52, 1.7), (0.38, 0.86, 1.7), 3),
    Scene("03-order-journey", 4.5, (0.5, 0.33, 1.6), (0.5, 0.33, 1.75), 3),
    Scene("03-order-journey", 6.0, (0.28, 0.56, 1.9), (0.72, 0.56, 1.9), 3),
    Scene("03-order-journey", 5.0, (0.3, 0.82, 1.7), (0.7, 0.82, 1.7), 3),
    Scene("04-price-path", 6.0, (0.3, 0.55, 1.5), (0.72, 0.55, 1.5), 3),
    Scene("05-safety", 6.0, (0.28, 0.58, 1.55), (0.76, 0.5, 1.45), 3),
    Scene("09-algos", 5.5, (*C, 1.25), (*C, 1.0), 3),
    # 4. running it
    Scene(
        "card:PART THREE|Run it for a whole firm|Firms, users, limits, monitoring, deployment.",
        3.5,
        act=4,
    ),
    Scene("shot:admin.png|ADMIN|Firms, users and limits: each firm sees only its own", 4.5, act=4),
    Scene("02-architecture", 6.0, (0.86, 0.3, 1.9), (0.86, 0.72, 1.9), 4),
    # 5. quality and the finale
    Scene("card:|Every order checked.|Every price live. Every rule from the server.", 4.0, act=5),
    Scene(
        "card:|Your desk. Your rules.|Paper trade on live prices. Go live on Zerodha Kite.",
        4.5,
        act=5,
    ),
    Scene("outro", 6.0, (*C, 1.0), (*C, 1.08), 5),
]

TRANSITIONS = [
    "fade",
    "smoothleft",
    "circleopen",
    "smoothup",
    "dissolve",
    "slideleft",
    "radial",
    "smoothright",
]

CARD = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}">
<style>.c{{position:absolute;inset:0;display:grid;place-items:center;text-align:center;padding:0 120px}}
.k{{font-size:20px;letter-spacing:.34em;font-weight:700;color:var(--cyan)}}
.h{{font-family:"Inter Display";font-weight:850;font-size:{size}px;letter-spacing:-.03em;line-height:1.05;margin-top:18px}}
.s{{font-size:28px;color:var(--dim);margin-top:24px}}</style></head>
<body><div class="c"><div><div class="k">{kicker}</div><div class="h"><span class="grad">{headline}</span></div><div class="s">{sub}</div></div></div></body></html>"""

SHOT = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}">
<style>.cap{{position:absolute;left:64px;top:44px}}.k{{font-size:16px;letter-spacing:.3em;font-weight:700;color:var(--cyan)}}
.h{{font-family:"Inter Display";font-weight:800;font-size:40px;letter-spacing:-.02em;margin-top:6px}}
.f{{position:absolute;left:50%;top:150px;transform:translateX(-50%);border-radius:14px;overflow:hidden;
border:1px solid rgba(148,170,220,.35);box-shadow:0 40px 120px rgba(0,0,0,.7),0 0 120px rgba(34,211,238,.12)}}
.f img{{display:block;max-width:1440px;max-height:800px}}</style></head>
<body><div class="cap"><div class="k">{kicker}</div><div class="h">{headline}</div></div>
<div class="f"><img src="{img}"></div></body></html>"""


def draw_pages(scenes: list[Scene], work: Path) -> list[Path]:
    """The PNG for each scene: posters as they are, cards and shots drawn here."""
    css = (SHOWCASE / "style.css").as_uri()
    pages, pngs = [], []
    for i, s in enumerate(scenes):
        if s.picture.startswith(("card:", "shot:")):
            kind, rest = s.picture.split(":", 1)
            a, b, c = (html.escape(p) for p in rest.split("|"))
            if kind == "card":
                size = 96 if len(b) < 40 else 74
                page = CARD.format(css=css, kicker=a, headline=b, sub=c, size=size)
            else:
                img = (SHOTS / rest.split("|")[0]).as_uri()
                page = SHOT.format(css=css, kicker=b, headline=c, img=img)
            src, png = work / f"page{i}.html", work / f"page{i}.png"
            src.write_text(page)
            pages.append({"src": src.as_uri(), "png": str(png)})
            pngs.append(png)
        elif s.picture == "outro":
            png = work / "outro.png"
            if not any(p["png"] == str(png) for p in pages):
                pages.append({"src": (HERE / "outro.html").as_uri(), "png": str(png)})
            pngs.append(png)
        else:
            pngs.append(POSTERS / f"{s.picture}.png")
    js = """const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  for (const x of JSON.parse(process.argv[1])) {
    await p.goto(x.src); await p.evaluate(() => document.fonts.ready); await p.screenshot({ path: x.png });
  }
  await b.close();
})();"""
    subprocess.run(["node", "-e", js, json.dumps(pages)], check=True)
    return pngs


def film(png: Path, s: Scene, mp4: Path) -> None:
    """One scene: the camera glides from s.start to s.end, eased at both ends."""
    frames = int(s.seconds * FPS)
    (x0, y0, z0), (x1, y1, z1) = s.start, s.end
    p = f"(on/{frames})"
    e = f"({p}*{p}*(3-2*{p}))"  # smoothstep: slow start, slow end
    zoom = f"{z0}+({z1 - z0})*{e}"
    x = f"max(0,min(iw-iw/zoom,({x0}+({x1 - x0})*{e})*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,({y0}+({y1 - y0})*{e})*ih-ih/zoom/2))"
    vf = f"scale=3840:2400,zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s=1920x1200:fps={FPS},format=yuv420p"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(png),
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-crf",
            "16",
            str(mp4),
        ],
        check=True,
    )


def join(clips: list[Path], scenes: list[Scene], mp4: Path) -> list[float]:
    """Joins the scenes with transitions; returns when each cut happens (seconds)."""
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        args += ["-i", str(c)]
    chain, last, offset, cuts = [], "[0:v]", 0.0, []
    for i in range(1, len(clips)):
        offset += scenes[i - 1].seconds - FADE
        cuts.append(offset)
        kind = TRANSITIONS[i % len(TRANSITIONS)]
        chain.append(
            f"{last}[{i}:v]xfade=transition={kind}:duration={FADE}:offset={offset:.3f}[v{i}]"
        )
        last = f"[v{i}]"
    total = offset + scenes[-1].seconds
    chain.append(f"{last}fade=t=in:st=0:d=1,fade=t=out:st={total - 2}:d=2[v]")
    args += [
        "-filter_complex",
        ";".join(chain),
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "slow",
        str(mp4),
    ]
    subprocess.run(args, check=True)
    return [total] + cuts


def tone(freq: float, t: np.ndarray) -> np.ndarray:
    return (
        np.sin(2 * np.pi * freq * t)
        + 0.3 * np.sin(4 * np.pi * freq * t)
        + 0.12 * np.sin(6 * np.pi * freq * t)
    )


def soundtrack(seconds: float, cuts: list[float], acts: list[tuple[float, int]], wav: Path) -> None:
    """acts: (start second, act number). Act 1 is a pad; 2 adds bass and a beat; 3 adds an
    arpeggio; 4 keeps it; 5 drops back to the pad and bass for the ending."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    beat = 60 / 100
    bar = 4 * beat
    chords = [
        (220.0, 261.63, 329.63),
        (174.61, 220.0, 261.63),
        (130.81, 196.0, 261.63),
        (196.0, 246.94, 293.66),
    ]
    bass = [110.0, 87.31, 65.41, 98.0]
    arp = [
        [440.0, 523.25, 659.25, 880.0],
        [349.23, 440.0, 523.25, 698.46],
        [392.0, 523.25, 659.25, 783.99],
        [392.0, 493.88, 587.33, 783.99],
    ]

    def act_at(sec: float) -> int:
        return max((a for s, a in acts if s <= sec), default=1)

    mix = np.zeros(n)
    rng = np.random.default_rng(7)
    for start in np.arange(0, seconds, bar):
        k = int(round(start / bar)) % 4
        a, b = int(start * RATE), min(n, int((start + bar) * RATE))
        if a >= b:
            continue
        tt = t[a:b] - start
        act = act_at(start)
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.4)
        mix[a:b] += 0.2 * env * sum(tone(f, t[a:b]) for f in chords[k]) / 3
        if act >= 2:
            for half in (0, 2 * beat):
                s, e = a + int(half * RATE), min(b, a + int((half + 1.6 * beat) * RATE))
                if s < e:
                    mix[s:e] += (
                        0.26 * np.sin(2 * np.pi * bass[k] * t[s:e]) * np.exp(-(t[s:e] - t[s]) * 2.2)
                    )
        if act in (2, 3, 4):
            for q in range(4):
                s, e = a + int(q * beat * RATE), min(b, a + int((q * beat + 0.25) * RATE))
                if s < e:
                    kt = t[s:e] - t[s]
                    mix[s:e] += (
                        0.45
                        * np.sin(2 * np.pi * (50 + 90 * np.exp(-kt * 30)) * kt)
                        * np.exp(-kt * 14)
                    )
                h, he = (
                    a + int((q + 0.5) * beat * RATE),
                    min(b, a + int(((q + 0.5) * beat + 0.05) * RATE)),
                )
                if h < he:
                    mix[h:he] += 0.05 * rng.standard_normal(he - h) * np.exp(-(t[h:he] - t[h]) * 60)
        if act in (3, 4):
            step = beat / 4
            for q in range(16):
                s, e = a + int(q * step * RATE), min(b, a + int((q * step + 0.22) * RATE))
                if s < e:
                    f = arp[k][q % 4] * (2 if q >= 8 and act == 4 else 1)
                    mix[s:e] += (
                        0.07 * np.sin(2 * np.pi * f * t[s:e]) * np.exp(-(t[s:e] - t[s]) * 12)
                    )
    for c in cuts:  # a whoosh into every cut: noise swelling up and away
        s, e = max(0, int((c - 0.35) * RATE)), min(n, int((c + 0.5) * RATE))
        wt = (t[s:e] - (c - 0.35)) / 0.85
        noise = np.convolve(rng.standard_normal(e - s), np.ones(12) / 12, mode="same")
        mix[s:e] += 0.18 * noise * np.sin(np.pi * np.clip(wt, 0, 1)) ** 2
    sw = int(2 * bar * RATE)  # a rising swell under the opening
    mix[:sw] += (
        0.1
        * np.sin(2 * np.pi * (110 + 110 * t[:sw] / (2 * bar)) * t[:sw])
        * (t[:sw] / (2 * bar)) ** 2
    )
    mix *= np.minimum(1, t / 2) * np.minimum(1, (seconds - t) / 4)
    mix = mix / np.max(np.abs(mix)) * 0.85
    right = np.concatenate([np.zeros(300), mix[:-300]])
    stereo = (np.stack([mix, right], axis=1) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(stereo.tobytes())


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pngs = draw_pages(SCENES, work)
        clips = []
        for i, (png, s) in enumerate(zip(pngs, SCENES, strict=True)):
            c = work / f"clip{i:02}.mp4"
            film(png, s, c)
            clips.append(c)
        video = work / "video.mp4"
        total, *cuts = join(clips, SCENES, video)
        starts, at = [], 0.0
        for s in SCENES:
            starts.append((at, s.act))
            at += s.seconds - FADE
        wav = work / "music.wav"
        soundtrack(total, cuts, starts, wav)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-i",
                str(wav),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(OUT),
            ],
            check=True,
        )
    print(f"wrote {OUT} ({total:.0f} s, {len(SCENES)} scenes)")


if __name__ == "__main__":
    main()
