"""Build the music bed for the intro video: a soft, self-synthesized chord pad.

Everything is generated locally and deterministically (no samples, no third-party
audio), so the result can be redistributed with the repository's licence.

    python audio/build_music.py          # writes audio/music.wav (63 s, 44.1 kHz mono)
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
DUR = 63.0
HERE = Path(__file__).parent
rng = np.random.default_rng(11)

# I – vi – IV – V in C, voiced softly; four seconds per chord.
CHORDS = [
    (48, 55, 64, 71),   # Cmaj7
    (45, 52, 60, 67),   # Am7
    (41, 48, 57, 64),   # Fmaj7
    (43, 50, 59, 62),   # G6
]
STEP = 4.0


def hz(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def main() -> None:
    n = int(SR * DUR)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float64)
    count = int(np.ceil(DUR / STEP))
    for k in range(count):
        start = k * STEP
        notes = CHORDS[k % len(CHORDS)]
        a, b = int(start * SR), min(n, int((start + STEP + 1.5) * SR))
        seg = t[a:b] - start
        env = np.minimum(seg / 1.2, 1.0) * np.exp(-np.maximum(seg - STEP, 0) / 0.6)
        for i, note in enumerate(notes):
            f = hz(note) * (1 + rng.uniform(-0.0008, 0.0008))
            tone = (np.sin(2 * np.pi * f * seg) + 0.25 * np.sin(4 * np.pi * f * seg)
                    + 0.08 * np.sin(6 * np.pi * f * seg))
            out[a:b] += 0.06 * env * tone * (0.9 if i else 1.1)
        # a quiet high "glint" on each chord change
        g = np.exp(-seg / 0.9) * np.sin(2 * np.pi * hz(notes[-1] + 12) * seg)
        out[a:b] += 0.018 * g
    # slow tremolo, fades
    out *= 0.85 + 0.15 * np.sin(2 * np.pi * 0.11 * t)
    out *= np.minimum(t / 2.0, 1.0) * np.minimum((DUR - t) / 3.0, 1.0)
    out /= max(1e-9, np.abs(out).max()) / 0.32
    with wave.open(str(HERE / "music.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes())
    print("wrote", HERE / "music.wav")


if __name__ == "__main__":
    main()
