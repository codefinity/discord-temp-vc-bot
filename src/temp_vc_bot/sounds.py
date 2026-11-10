"""Make the beeps and the ring that /timer plays in a voice channel.

Discord voice takes raw sound as 16-bit, 48 kHz stereo samples, and that's what
this file makes, so the bot needs no sound files and no FFmpeg. This file only
makes sounds. services/user_timer.py decides when to play them, and
services/speaker.py plays them.
"""

from __future__ import annotations

import math
import sys
from array import array
from functools import cache

SAMPLE_RATE = 48_000
# Discord sends sound in 20 ms pieces: 960 samples, 2 channels, 2 bytes each.
# discord.py stops at a piece that's cut short, so every sound is padded to whole pieces.
FRAME_BYTES = 3840
# Silence before the first beep. The first moments of sound on a new voice connection
# can be cut off, so the count-in's first beep comes this long after playing starts.
LEAD_IN_SECONDS = 0.3

_VOLUME = 0.3  # A share of the loudest possible. People can also turn the bot down in Discord.
_FADE_SECONDS = 0.005  # Fading each tone in and out stops it clicking.
_BEEP_HZ = 880.0  # The count-in's beeps.
_BEEP_SECONDS = 0.15
_GO_HZ = 1760.0  # The longer, higher beep as the timer starts, an octave up.
_GO_SECONDS = 0.4
# The ring is an electric bell: two tones, struck this many times a second.
_RING_HZ = (1150.0, 2760.0)
_RING_STRIKES_PER_SECOND = 16
_RING_FADE_OUT_SECONDS = 0.1


@cache
def count_in(count: int, beat_seconds: float) -> bytes:
    """`count` short beeps, `beat_seconds` apart, then a higher beep one beat later, as the timer starts.

    The first beep comes LEAD_IN_SECONDS after playing starts. Each sound is made
    once and kept. Making one takes about a tenth of a second, so async code
    should call this in a thread.
    """
    go_at = LEAD_IN_SECONDS + count * beat_seconds
    track = [0.0] * _samples(go_at + _GO_SECONDS)
    # A beep can't be longer than the gap between beeps, or they'd run together.
    beep = _tone(_BEEP_HZ, min(_BEEP_SECONDS, beat_seconds))
    for number in range(count):
        _mix_in(track, beep, LEAD_IN_SECONDS + number * beat_seconds)
    _mix_in(track, _tone(_GO_HZ, _GO_SECONDS), go_at)
    return _to_pcm(track)


@cache
def ring(seconds: float) -> bytes:
    """An electric bell ringing for `seconds`, fading out at the end.

    Like count_in, it's made once and kept, and async code should call it in a thread.
    """
    total = _samples(seconds)
    fade_in, fade_out = _samples(_FADE_SECONDS), _samples(_RING_FADE_OUT_SECONDS)
    low, high = (2 * math.pi * hz / SAMPLE_RATE for hz in _RING_HZ)
    strike = math.pi * _RING_STRIKES_PER_SECOND / SAMPLE_RATE
    track = []
    for i in range(total):
        envelope = min(1.0, i / fade_in, (total - 1 - i) / fade_out)
        # Each half-wave of this is one strike of the bell's clapper.
        clapper = abs(math.sin(strike * i))
        track.append(envelope * clapper * (0.65 * math.sin(low * i) + 0.35 * math.sin(high * i)))
    return _to_pcm(track)


def duration(pcm: bytes) -> float:
    """How long a sound from this file plays, in seconds."""
    return len(pcm) / (SAMPLE_RATE * 2 * 2)


def _samples(seconds: float) -> int:
    return round(seconds * SAMPLE_RATE)


def _tone(hz: float, seconds: float) -> list[float]:
    """A plain tone, faded in and out."""
    total = _samples(seconds)
    fade = _samples(_FADE_SECONDS)
    step = 2 * math.pi * hz / SAMPLE_RATE
    return [min(1.0, i / fade, (total - 1 - i) / fade) * math.sin(step * i) for i in range(total)]


def _mix_in(track: list[float], sound: list[float], at_seconds: float) -> None:
    """Add `sound` into `track`, starting `at_seconds` in."""
    start = _samples(at_seconds)
    for i, value in enumerate(sound[: len(track) - start]):
        track[start + i] += value


def _to_pcm(track: list[float]) -> bytes:
    """Turn samples from -1 to 1 into what Discord voice takes, padded to whole pieces."""
    pcm = array("h")
    for value in track:
        sample = round(max(-1.0, min(1.0, value)) * _VOLUME * 32767)
        pcm.extend((sample, sample))  # The same sound in the left and right channels.
    if sys.byteorder == "big":
        pcm.byteswap()  # Discord wants the low byte first.
    data = pcm.tobytes()
    return data + bytes(-len(data) % FRAME_BYTES)
