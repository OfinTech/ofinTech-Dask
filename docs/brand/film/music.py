"""A quiet bed for the 90-second film: two slow chords, a little air, and a soft mark at each turn in the story.

Nothing here is meant to be noticed. It sits about 23 dB under full scale so a voice could be laid over it later
without touching the mix, and it works at low volume on a laptop speaker in a meeting room.
"""

import numpy as np
import wave

SR = 48_000
DUR = 90.0
N = int(SR * DUR)
t = np.arange(N) / SR

# the two chords: D minor settles the first half, C major lifts the sign-off
CHORD_A = [146.83, 220.00, 349.23]          # D3  A3  F4
CHORD_B = [130.81, 196.00, 329.63]          # C3  G3  E4
TURN = 46.0                                  # where one gives way to the other

# the moments the picture turns, where a single soft tone marks the cut
MARKS = [(7.0, 392.00, .55), (23.4, 587.33, .75), (42.6, 523.25, .70), (59.4, 659.25, .62),
         (65.6, 493.88, .62), (80.4, 783.99, .85)]


def envelope(x, attack, release):
    """a slow swell in, a slow fall away, so nothing starts or stops abruptly"""
    env = np.ones_like(x)
    a, r = int(attack * SR), int(release * SR)
    env[:a] = np.linspace(0, 1, a) ** 2
    env[-r:] = np.linspace(1, 0, r) ** 2
    return env


def pad(freqs, gate):
    """three voices, each slightly detuned against itself so the chord breathes instead of sitting still"""
    out = np.zeros(N)
    for i, f in enumerate(freqs):
        drift = 1 + 0.0009 * np.sin(2 * np.pi * (0.055 + 0.011 * i) * t + i)
        voice = np.sin(2 * np.pi * f * drift * t)
        voice += 0.26 * np.sin(2 * np.pi * 2 * f * drift * t)        # one octave up, quietly
        voice += 0.07 * np.sin(2 * np.pi * 3 * f * drift * t)
        swell = 0.84 + 0.16 * np.sin(2 * np.pi * (0.033 + 0.007 * i) * t)
        out += voice * swell * (0.62 if i == 0 else 0.40 if i == 1 else 0.28)
    return out * gate


def one_pole(x, cutoff):
    """a plain low-pass: takes the edge off the harmonics so the pad stays behind the picture"""
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def crossfade(start, length):
    """1 before `start`, 0 after; the two chords trade places over `length` seconds"""
    g = np.clip((t - start) / length, 0, 1)
    return 0.5 * (1 + np.cos(np.pi * g))


gate_a = crossfade(TURN - 3.0, 6.0)
mix = pad(CHORD_A, gate_a) + pad(CHORD_B, 1 - gate_a)

# a breath of air above the chord, filtered until it is only a texture
rng = np.random.default_rng(7)
air = one_pole(rng.standard_normal(N), 900) * 0.030
air *= 0.55 + 0.45 * np.sin(2 * np.pi * 0.021 * t + 1.2)
mix += air

# the marks: a soft struck tone that decays, never a chime
for at, freq, amp in MARKS:
    i0 = int(at * SR)
    span = min(N - i0, int(3.6 * SR))
    local = np.arange(span) / SR
    decay = np.exp(-local * 1.45)
    tone = (np.sin(2 * np.pi * freq * local) + 0.30 * np.sin(2 * np.pi * freq * 2.004 * local)
            + 0.10 * np.sin(2 * np.pi * freq * 3.01 * local))
    attack = np.clip(local / 0.035, 0, 1)
    mix[i0:i0 + span] += tone * decay * attack * amp * 0.085

mix = one_pole(mix, 2600)
mix *= envelope(mix, 2.6, 4.0)

# a hair of width: the right channel delayed by a few milliseconds and rolled off
delay = int(0.011 * SR)
right = np.concatenate([np.zeros(delay), mix[:-delay]]) * 0.93
stereo = np.stack([mix, 0.5 * mix + 0.5 * right], axis=1)

# sit it low: about -23 dBFS RMS, with the peaks well clear of the ceiling
rms = np.sqrt(np.mean(stereo ** 2))
stereo *= (10 ** (-23 / 20)) / rms
peak = np.max(np.abs(stereo))
if peak > 0.72:
    stereo *= 0.72 / peak

pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
with wave.open("music.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())

print(f"music.wav  {DUR:.0f}s  RMS {20 * np.log10(np.sqrt(np.mean(stereo ** 2))):.1f} dBFS  "
      f"peak {20 * np.log10(np.max(np.abs(stereo))):.1f} dBFS")
