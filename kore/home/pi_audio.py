"""The box's microphone (Raspberry Pi): one turn of listening, after the wake word or the button.

- arecord_frames: the microphone through ALSA's `arecord` (16 kHz, mono, 16-bit little-endian), in 30 ms frames.
- endpoint: decides when they have finished. The first 300 ms measure the room's noise; speech is a few frames
  well above it; once they have spoken, 1.3 s of quiet ends the turn. Nothing said in 8 s: nothing heard.
  20 s at most. Only the words (with a little margin) are kept, so less audio is sent and paid for.
- PiEars: records, then asks the speech-to-text (ElevenSTT, kore/home/eleven.py) and hands over the same `Heard`
  as MacEars (kore/home/mac.py), so the home loop takes either.

No new dependency: `array` for the sound level (audioop is gone in Python 3.13), `wave` for the file.
The recorder is injectable: tests feed made-up frames, no audio device.
"""

from __future__ import annotations

import io
import math
import select
import subprocess
import sys
import time
import wave
from array import array
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from kore.home.mac import ERRORS as MAC_ERRORS
from kore.home.mac import Heard

RATE = 16000
STALL_S = 2.0  # no sound data from the microphone for this long: stop
FRAME_MS = 30
FRAME_SAMPLES = RATE * FRAME_MS // 1000   # 480
FRAME_BYTES = FRAME_SAMPLES * 2           # 960

CALIBRATE_MS = 300
END_SILENCE_MS = 1300
NO_SPEECH_MS = 8000
MAX_MS = 20000
START_MS = 90         # this long above the bar before it counts as speech (a click or a knock doesn't)
PAD_MS = 300          # kept before the first word and after the last

FLOOR = 300.0         # RMS (of 32768): below this it is never speech, however quiet the room
NOISE_CAP = 600.0     # a room noise estimate above this is someone already talking, not the room
START_FACTOR = 3.0    # speech starts this far above the room's noise...
HOLD_FACTOR = 2.0     # ...and goes on while it stays this far above it (soft endings of words)

ERRORS = {
    **MAC_ERRORS,
    "stt_no_connection": "No tengo conexión a internet para entender lo que me dicen.",
    "stt_busy": "El servicio que me ayuda a entender está ocupado. Probamos otra vez en un momento.",
    "stt_bad_key": "La clave de ElevenLabs no funciona o se ha gastado: hay que avisar a Lea.",
    "stt_rejected": "No he podido entender esa grabación.",
}


def _frames(ms: int) -> int:
    return math.ceil(ms / FRAME_MS)


def rms(frame: bytes) -> float:
    """Root mean square of 16-bit little-endian samples."""
    samples = array("h")
    samples.frombytes(frame[: len(frame) // 2 * 2])
    if not samples:
        return 0.0
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples))


@dataclass
class Recording:
    wav: bytes                 # 16 kHz mono 16-bit WAV of the words; b"" if nothing was said
    ended_at: float | None     # monotonic time they stopped speaking
    reason: str                # speech | too_long | no_speech | mic_error

    @property
    def speech(self) -> bool:
        return self.reason in ("speech", "too_long")


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def endpoint(frames: Iterable[bytes], clock: Callable[[], float] = time.monotonic) -> Recording:
    """Reads 30 ms frames until the turn is over (see the module docstring)."""
    cal_n, start_n, end_n = _frames(CALIBRATE_MS), _frames(START_MS), _frames(END_SILENCE_MS)
    no_speech_n, max_n, pad_n = _frames(NO_SPEECH_MS), _frames(MAX_MS), _frames(PAD_MS)
    kept: list[bytes] = []
    levels: list[float] = []
    start = hold = None           # the bars, once the room is measured
    checked = 0                   # frames already judged
    run = 0                       # frames in a row above the start bar
    first = last = None           # first and last frames of speech
    reason = None

    def judge(i: int) -> None:
        nonlocal run, first, last
        level = levels[i]
        run = run + 1 if level >= start else 0
        if first is None:
            if run >= start_n:
                first, last = i - run + 1, i
        elif level >= hold:
            last = i

    for frame in frames:
        kept.append(frame)
        levels.append(rms(frame))
        n = len(kept)
        if start is None:
            if n < cal_n:
                continue
            noise = min(sorted(levels)[len(levels) // 4], NOISE_CAP)  # a low one: they may start talking at once
            start, hold = max(FLOOR, noise * START_FACTOR), max(FLOOR * 0.6, noise * HOLD_FACTOR)
        while checked < n:
            judge(checked)
            checked += 1
        if first is not None and (n - 1) - last >= end_n:
            reason = "speech"
        elif first is None and n >= no_speech_n:
            reason = "no_speech"
        elif n >= max_n:
            reason = "too_long" if first is not None else "no_speech"
        if reason:
            break
    else:  # the microphone stopped
        if not kept:
            return Recording(b"", None, "mic_error")
        if start is None:  # shorter than the room measurement: judge what there is
            noise = min(sorted(levels)[len(levels) // 4], NOISE_CAP)
            start, hold = max(FLOOR, noise * START_FACTOR), max(FLOOR * 0.6, noise * HOLD_FACTOR)
            for i in range(len(kept)):
                judge(i)
        reason = "speech" if first is not None else "no_speech"

    if first is None:
        return Recording(b"", None, "no_speech")
    now = clock()
    ended_at = now if reason == "too_long" else now - ((len(kept) - 1) - last) * FRAME_MS / 1000
    pcm = b"".join(kept[max(0, first - pad_n): last + 1 + pad_n])
    return Recording(_wav(pcm), ended_at, reason)


def arecord_frames(device: str = "default", popen=subprocess.Popen) -> Iterator[bytes]:
    """The microphone, 30 ms at a time, until the caller stops reading (then arecord is stopped)."""
    proc = popen(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            # A microphone that stops sending (unplugged, a stuck driver) must not hang Pepe: 2 s of nothing ends it.
            if not _readable(proc.stdout, STALL_S):
                return
            chunk = proc.stdout.read(FRAME_BYTES)
            if not chunk or len(chunk) < FRAME_BYTES:
                return
            yield chunk
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            pass


def _readable(f, timeout: float) -> bool:
    """Data within `timeout` seconds? (Something without a file number, like a test's buffer, always is.)"""
    try:
        fd = f.fileno()
    except (OSError, ValueError, AttributeError):
        return True
    return bool(select.select([fd], [], [], timeout)[0])


def until(frames: Iterable[bytes], stop: Callable[[], bool]) -> Iterator[bytes]:
    """The microphone until `stop()` (a help press while listening): listening ends at once."""
    it = iter(frames)
    try:
        for chunk in it:
            if stop():
                return
            yield chunk
    finally:
        close = getattr(it, "close", None)
        if close:
            close()


def capture(source: Callable[[], Iterable[bytes]] | None = None,
            clock: Callable[[], float] = time.monotonic) -> Recording:
    """One turn from the microphone (arecord unless a source is given). Never raises."""
    source = source or arecord_frames
    try:
        frames = source()
        try:
            return endpoint(frames, clock)
        finally:
            close = getattr(frames, "close", None)
            if close:
                close()
    except (OSError, subprocess.SubprocessError, ValueError):
        return Recording(b"", None, "mic_error")


class PiEars:
    """One turn of listening on the box: the same `listen() -> Heard` as MacEars."""

    def __init__(self, stt, record: Callable[[], Recording] | None = None, on_ready=None, language: str = "es"):
        self.stt = stt
        self._record = record or capture
        self.on_ready = on_ready  # called just before it listens (the "tin"), so it doesn't hear itself
        self.language = language

    def listen(self) -> Heard:
        if self.on_ready:
            self.on_ready()
        rec = self._record()
        if rec.reason == "mic_error":
            return Heard("", error="mic_start", on_device=False)
        if not rec.speech:
            return Heard("", None, None, on_device=False)  # nothing said: nothing sent, nothing paid
        text = self.stt.transcribe(rec.wav, self.language)
        if text is None:
            err = getattr(self.stt, "last_error", None) or "rejected"
            return Heard("", rec.ended_at, f"stt_{err}", on_device=False)
        return Heard(text, rec.ended_at, None, on_device=False)
