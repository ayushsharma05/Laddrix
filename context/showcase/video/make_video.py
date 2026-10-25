"""Builds the MockTrade showcase video (#455) from the posters in context/images/showcase/.

    python3 context/showcase/video/make_video.py

1. draws the end card (outro.html) with Playwright, next to the posters;
2. makes a slow zoom clip of each poster and joins them with crossfades (ffmpeg);
3. writes a soundtrack from plain sine waves (numpy): a chord pad, a bass line, a soft
   kick on each beat and a rising swell at the start; every note is computed here, so
   there is nothing to license;
4. puts the two together in context/showcase/video/mocktrade-showcase.mp4.
"""

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
IMAGES = HERE.parent.parent / "images" / "showcase"
OUT = HERE / "mocktrade-showcase.mp4"
FPS = 30
CLIP = 6.0  # seconds each picture shows
FADE = 1.0  # seconds of crossfade between pictures
RATE = 44100


def draw_outro(png: Path) -> None:
    js = f"""
const {{ chromium }} = require("playwright");
(async () => {{
  const b = await chromium.launch();
  const p = await b.newPage({{ viewport: {{ width: 1600, height: 1000 }}, deviceScaleFactor: 2 }});
  await p.goto("file://{HERE / 'outro.html'}");
  await p.evaluate(() => document.fonts.ready);
  await p.screenshot({{ path: "{png}" }});
  await b.close();
}})();"""
    subprocess.run(["node", "-e", js], check=True)


def clip(png: Path, mp4: Path, zoom_in: bool) -> None:
    """One picture as a CLIP-second video, zooming slowly in (or out) on its centre."""
    frames = int(CLIP * FPS)
    z = f"1+0.06*on/{frames}" if zoom_in else f"1.06-0.06*on/{frames}"
    vf = (
        f"scale=3840:2400,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s=1920x1200:fps={FPS},format=yuv420p"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-vf", vf, "-frames:v", str(frames), str(mp4)],
        check=True,
    )


def join(clips: list[Path], mp4: Path) -> float:
    """Crossfades the clips one into the next; returns the length in seconds."""
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        args += ["-i", str(c)]
    chain, last = [], "[0:v]"
    for i in range(1, len(clips)):
        out = f"[v{i}]"
        offset = i * (CLIP - FADE)
        chain.append(f"{last}[{i}:v]xfade=transition=smoothleft:duration={FADE}:offset={offset}{out}")
        last = out
    total = len(clips) * CLIP - (len(clips) - 1) * FADE
    chain.append(f"{last}fade=t=in:st=0:d=1,fade=t=out:st={total - 1.5}:d=1.5[v]")
    args += ["-filter_complex", ";".join(chain), "-map", "[v]", "-c:v", "libx264", "-crf", "20", "-preset", "slow", str(mp4)]
    subprocess.run(args, check=True)
    return total


def note(freq: float, t: np.ndarray) -> np.ndarray:
    """A soft tone: a sine with two quiet overtones."""
    return np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)


def soundtrack(seconds: float, wav: Path) -> None:
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    bpm = 96
    beat = 60 / bpm
    bar = 4 * beat
    # A minor, F, C, G: one chord per bar (frequencies in Hz)
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (130.81, 196.0, 261.63), (196.0, 246.94, 293.66)]
    bass = [110.0, 87.31, 65.41, 98.0]
    mix = np.zeros(n)
    for start in np.arange(0, seconds, bar):
        k = int(round(start / bar)) % 4
        a, b = int(start * RATE), min(n, int((start + bar) * RATE))
        tt = t[a:b] - start
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.4)  # swell in, ease out
        pad = sum(note(f, t[a:b]) for f in chords[k]) / 3
        mix[a:b] += 0.22 * pad * env
        for half in (0, 2 * beat):  # bass on beats 1 and 3
            s = a + int(half * RATE)
            e = min(b, s + int(1.6 * beat * RATE))
            if s >= e:
                continue  # the last bar is cut short by the video's end
            bt = t[s:e] - t[s]
            mix[s:e] += 0.28 * np.sin(2 * np.pi * bass[k] * t[s:e]) * np.exp(-bt * 2.2)
    # a soft kick on every beat after the first two bars, and a light shimmer on the off-beats
    for start in np.arange(2 * bar, seconds - bar, beat):
        s = int(start * RATE)
        e = min(n, s + int(0.25 * RATE))
        kt = t[s:e] - t[s]
        mix[s:e] += 0.5 * np.sin(2 * np.pi * (50 + 90 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 14)
        h = s + int(beat / 2 * RATE)
        he = min(n, h + int(0.05 * RATE))
        rng = np.random.default_rng(int(start * 1000))
        mix[h:he] += 0.05 * rng.standard_normal(he - h) * np.exp(-(t[h:he] - t[h]) * 60)
    # a rising swell over the first bar
    sw = int(bar * RATE)
    mix[:sw] += 0.12 * np.sin(2 * np.pi * (220 + 220 * t[:sw] / bar) * t[:sw]) * (t[:sw] / bar) ** 2
    # fade in and out, then normalise
    mix *= np.minimum(1, t / 1.5) * np.minimum(1, (seconds - t) / 3)
    mix = mix / np.max(np.abs(mix)) * 0.85
    # a little stereo width: the right channel is the left a few milliseconds later
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
        outro = work / "outro.png"
        draw_outro(outro)
        pictures = sorted(IMAGES.glob("0*.png")) + [outro]
        clips = []
        for i, png in enumerate(pictures):
            c = work / f"clip{i}.mp4"
            clip(png, c, zoom_in=i % 2 == 0)
            clips.append(c)
        video = work / "video.mp4"
        seconds = join(clips, video)
        wav = work / "music.wav"
        soundtrack(seconds, wav)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)],
            check=True,
        )
    print(f"wrote {OUT} ({seconds:.0f} s)")


if __name__ == "__main__":
    main()
