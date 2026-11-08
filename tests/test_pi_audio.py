"""The box's microphone: when they have finished speaking (endpointing), on made-up sound. No audio device."""

import io
import math
import random
import wave
from array import array

import pytest

from kore.home import pi_audio
from kore.home.mac import Heard
from kore.home.pi_audio import PiEars, Recording, endpoint, rms

N = pi_audio.FRAME_SAMPLES  # 480 samples = 30 ms at 16 kHz


def quiet(level=60, seed=1):
    rnd = random.Random(seed)
    return array("h", [int(rnd.uniform(-level, level)) for _ in range(N)]).tobytes()


def voiced(level=4000, hz=220):
    return array("h", [int(level * math.sin(2 * math.pi * hz * i / 16000)) for i in range(N)]).tobytes()


def ms(n):
    return round(n / pi_audio.FRAME_MS)


class Feed:
    """A list of frames, then (optionally) the same frame forever; counts what was read."""

    def __init__(self, frames, then=None):
        self.frames, self.then, self.read = list(frames), then, 0

    def __iter__(self):
        for f in self.frames:
            self.read += 1
            yield f
        while self.then is not None:
            self.read += 1
            yield self.then


def duration(wav):
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return w.getnframes() / 16000


def test_rms():
    assert rms(b"") == 0
    assert rms(array("h", [100, -100] * 240).tobytes()) == pytest.approx(100)
    assert rms(array("h", [3, 4] * 240).tobytes()) == pytest.approx(math.sqrt(12.5))
    assert rms(voiced(1000)) == pytest.approx(1000 / math.sqrt(2), rel=0.02)


def test_silence_stops_after_8_seconds_with_nothing():
    feed = Feed([], then=quiet())
    rec = endpoint(feed, clock=lambda: 50.0)
    assert (rec.reason, rec.wav, rec.ended_at, rec.speech) == ("no_speech", b"", None, False)
    assert feed.read == math.ceil(8000 / 30)


def test_speech_then_silence_stops_after_1_3_seconds_and_keeps_only_the_words():
    feed = Feed([quiet()] * ms(600) + [voiced()] * ms(1500), then=quiet())
    rec = endpoint(feed, clock=lambda: 100.0)
    assert rec.reason == "speech" and rec.speech
    assert feed.read == ms(600) + ms(1500) + math.ceil(1300 / 30)
    assert rec.ended_at == pytest.approx(100.0 - math.ceil(1300 / 30) * 0.03)
    assert duration(rec.wav) == pytest.approx(1.5 + 2 * pi_audio.PAD_MS / 1000, abs=0.001)


def test_short_pauses_between_words_dont_end_it():
    words = ([voiced()] * ms(600) + [quiet()] * ms(900)) * 3
    feed = Feed([quiet()] * ms(300) + words, then=quiet())
    rec = endpoint(feed, clock=lambda: 0.0)
    assert rec.reason == "speech" and duration(rec.wav) > 3.0


def test_speaking_from_the_very_start_still_counts():
    feed = Feed([voiced()] * ms(2000), then=quiet())
    rec = endpoint(feed, clock=lambda: 0.0)
    assert rec.reason == "speech"
    assert duration(rec.wav) == pytest.approx(ms(2000) * 0.03 + pi_audio.PAD_MS / 1000, abs=0.001)


def test_never_stops_talking_is_cut_at_20_seconds():
    feed = Feed([quiet()] * ms(300), then=voiced())
    rec = endpoint(feed, clock=lambda: 7.0)
    assert rec.reason == "too_long" and rec.speech and rec.ended_at == 7.0
    assert feed.read == math.ceil(20000 / 30)


def test_a_click_or_a_cough_is_not_speech():
    feed = Feed([quiet()] * ms(1000) + [voiced(8000)] * 2, then=quiet())
    assert endpoint(feed, clock=lambda: 0.0).reason == "no_speech"


def test_a_noisy_room_raises_the_bar():
    hum = voiced(1500, hz=100)  # the telly, a fan: steady and loud
    assert endpoint(Feed([hum] * ms(1000), then=hum), clock=lambda: 0.0).reason == "no_speech"
    feed = Feed([hum] * ms(600) + [voiced(9000)] * ms(1000), then=hum)
    rec = endpoint(feed, clock=lambda: 0.0)
    assert rec.reason == "speech" and duration(rec.wav) < 2.0


def test_the_microphone_stops_mid_sentence():
    rec = endpoint(Feed([quiet()] * ms(600) + [voiced()] * ms(900)), clock=lambda: 3.0)
    assert rec.reason == "speech" and rec.ended_at == 3.0
    assert endpoint(Feed([]), clock=lambda: 0.0).reason == "mic_error"


def test_arecord_is_asked_for_16khz_mono_s16le_and_stopped_after():
    class Proc:
        def __init__(self, cmd, **kw):
            Proc.cmd, Proc.killed = cmd, False
            self.stdout = io.BytesIO((quiet() * ms(300)) + voiced() * ms(1000) + quiet() * ms(2000))

        def terminate(self):
            Proc.killed = True

        def wait(self, timeout=None):
            return 0

    rec = pi_audio.capture(lambda: pi_audio.arecord_frames(popen=Proc), clock=lambda: 0.0)
    assert rec.reason == "speech"
    assert Proc.cmd == ["arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
    assert Proc.killed


def test_no_arecord_is_a_mic_error():
    def popen(cmd, **kw):
        raise FileNotFoundError("arecord")

    assert pi_audio.capture(lambda: pi_audio.arecord_frames(popen=popen)).reason == "mic_error"


# --- PiEars: the same Heard as on the Mac ---------------------------------------------------------------------


class STT:
    def __init__(self, text="Pepe, ¿qué hora es?", error=None):
        self.text, self.last_error, self.got = text, error, []

    def transcribe(self, wav, language="es"):
        self.got.append((wav, language))
        return self.text


def test_ears_hand_over_the_words_and_when_they_ended():
    order = []
    stt = STT()
    ears = PiEars(stt, record=lambda: order.append("rec") or Recording(b"WAV", 12.5, "speech"),
                  on_ready=lambda: order.append("ready"))
    heard = ears.listen()
    assert isinstance(heard, Heard) and heard == Heard("Pepe, ¿qué hora es?", 12.5, None, False)
    assert stt.got == [(b"WAV", "es")] and order == ["ready", "rec"]


def test_nothing_said_costs_nothing():
    stt = STT()
    heard = PiEars(stt, record=lambda: Recording(b"", None, "no_speech")).listen()
    assert heard == Heard("", None, None, False) and stt.got == []


def test_ears_failures_have_their_own_messages():
    heard = PiEars(STT(), record=lambda: Recording(b"", None, "mic_error")).listen()
    assert heard.error == "mic_start" and heard.error in pi_audio.ERRORS
    for err in ("no_connection", "busy", "bad_key", "rejected"):
        heard = PiEars(STT(None, err), record=lambda: Recording(b"W", 1.0, "speech")).listen()
        assert heard.text == "" and heard.error == f"stt_{err}" and heard.error in pi_audio.ERRORS


def test_whole_way_from_frames_to_words():
    stt = STT("Hola")
    frames = [quiet()] * ms(300) + [voiced()] * ms(800) + [quiet()] * ms(1500)
    heard = PiEars(stt, record=lambda: pi_audio.capture(lambda: iter(frames), clock=lambda: 9.0)).listen()
    assert heard.text == "Hola" and heard.ended_at == pytest.approx(9.0 - math.ceil(1300 / 30) * 0.03)
    assert duration(stt.got[0][0]) == pytest.approx(ms(800) * 0.03 + 2 * pi_audio.PAD_MS / 1000, abs=0.001)
