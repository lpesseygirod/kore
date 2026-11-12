"""ElevenLabs as Pepe's ears (Scribe) and mouth (text to speech), against a server on this computer.

Fake key only: nothing leaves the computer. Switched off until ELEVENLABS_API_KEY is set.
"""

import io
import json
import threading
import wave
from array import array
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kore.home import eleven
from kore.home.eleven import ElevenSTT, ElevenVoice

KEY = "SECRET-xi-key"


class Eleven(BaseHTTPRequestHandler):
    seen: list = []
    status = 200
    body = b""
    ctype = "application/json"

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        raw = self.rfile.read(n)
        Eleven.seen.append({"path": self.path, "key": self.headers.get("xi-api-key"),
                            "ctype": self.headers.get("Content-Type"), "raw": raw})
        self.send_response(Eleven.status)
        self.send_header("Content-Type", Eleven.ctype)
        self.send_header("Content-Length", str(len(Eleven.body)))
        self.end_headers()
        self.wfile.write(Eleven.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def base():
    Eleven.seen, Eleven.status, Eleven.body, Eleven.ctype = [], 200, b"", "application/json"
    srv = HTTPServer(("127.0.0.1", 0), Eleven)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def form(seen) -> dict:
    """The multipart form the server got: {name: (filename, content type, bytes)}."""
    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + seen["ctype"].encode() + b"\r\n\r\n"
                                                      + seen["raw"])
    return {p.get_param("name", header="content-disposition"): (p.get_filename(), p.get_content_type(),
                                                                 p.get_payload(decode=True))
            for p in msg.iter_parts()}


def wav_of(pcm: bytes, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def samples(wav: bytes) -> tuple[int, list[int]]:
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth()) == (1, 2)
        return w.getframerate(), list(array("h", w.readframes(w.getnframes())))


PCM_1S = array("h", [1000, -1000] * 8000).tobytes()  # one second at 16 kHz


# --- switched off until the key is set -------------------------------------------------------------------


def test_off_without_the_key():
    assert eleven.from_env({}) == (None, None)
    assert eleven.from_env({"ELEVENLABS_API_KEY": "  \n"}) == (None, None)


def test_on_with_the_key_and_the_voice_is_configurable():
    stt, voice = eleven.from_env({"ELEVENLABS_API_KEY": f" {KEY}\n"})
    assert isinstance(stt, ElevenSTT) and isinstance(voice, ElevenVoice)
    assert voice.voice_id == eleven.DEFAULT_VOICE_ID and voice.model == eleven.TTS_MODEL
    _, voice = eleven.from_env({"ELEVENLABS_API_KEY": KEY, "ELEVENLABS_VOICE_ID": "abc123 "})
    assert voice.voice_id == "abc123"
    assert KEY not in repr(stt) and KEY not in repr(voice)


# --- speech to text (Scribe) -----------------------------------------------------------------------------------


def test_transcribe_sends_16khz_pcm_as_scribe_wants_it(base):
    Eleven.body = json.dumps({"language_code": "spa", "text": " Pepe, ¿qué tiempo hace? ", "words": []}).encode()
    stt = ElevenSTT(KEY, base=base, timeout=2)
    assert stt.transcribe(wav_of(PCM_1S)) == "Pepe, ¿qué tiempo hace?"
    s = Eleven.seen[-1]
    assert s["path"] == "/v1/speech-to-text" and s["key"] == KEY
    assert s["ctype"].startswith("multipart/form-data; boundary=")
    f = form(s)
    assert f["model_id"][2] == b"scribe_v2" and f["language_code"][2] == b"es"
    assert f["tag_audio_events"][2] == b"false" and f["file_format"][2] == b"pcm_s16le_16"
    assert f["file"][2] == PCM_1S  # the WAV header is taken off: raw PCM is faster for Scribe
    assert set(f) == {"model_id", "language_code", "tag_audio_events", "file_format", "file"}


def test_other_audio_goes_as_it_is(base):
    Eleven.body = json.dumps({"text": "hola"}).encode()
    stt = ElevenSTT(KEY, base=base, timeout=2)
    wav = wav_of(array("h", [500] * 22050).tobytes(), rate=22050)
    assert stt.transcribe(wav, language="ca") == "hola"
    f = form(Eleven.seen[-1])
    assert "file_format" not in f and f["file"][2] == wav and f["language_code"][2] == b"ca"


def test_too_short_to_be_anything_is_not_sent(base):
    stt = ElevenSTT(KEY, base=base, timeout=2)
    assert stt.transcribe(wav_of(b"\0\0" * 800)) == "" and Eleven.seen == []  # 50 ms: Scribe needs 100 ms


@pytest.mark.parametrize("status,error", [(401, "bad_key"), (429, "busy"), (500, "busy"), (422, "rejected")])
def test_transcribe_failures_are_none_and_never_raise(base, status, error):
    Eleven.status, Eleven.body = status, json.dumps({"detail": {"status": "x", "message": "nope"}}).encode()
    stt = ElevenSTT(KEY, base=base, timeout=2)
    assert stt.transcribe(wav_of(PCM_1S)) is None and stt.last_error == error
    assert KEY not in repr(stt)


def test_transcribe_garbage_answer_is_none(base):
    Eleven.body = b"<html>not json</html>"
    stt = ElevenSTT(KEY, base=base, timeout=2)
    assert stt.transcribe(wav_of(PCM_1S)) is None and stt.last_error == "rejected"


def test_transcribe_no_connection_is_none_without_the_key():
    stt = ElevenSTT(KEY, base="http://127.0.0.1:9", timeout=1)
    assert stt.transcribe(wav_of(PCM_1S)) is None
    assert stt.last_error == "no_connection" and KEY not in repr(stt)
    assert stt.transcribe(b"not a wav at all, but some bytes long enough" * 100) is None  # never raises


# --- text to speech -----------------------------------------------------------------------------------------------


class Player:
    def __init__(self, ok=True):
        self.ok, self.played = ok, []

    def __call__(self, wav: bytes) -> bool:
        self.played.append(wav)
        return self.ok


def tts(base, **kw):
    player = kw.pop("player", Player())
    return ElevenVoice(KEY, voice_id="VOICE1", base=base, timeout=2, player=player, **kw), player


def test_say_asks_for_pcm_and_plays_it_as_wav(base):
    Eleven.body, Eleven.ctype = array("h", [10000, -10000] * 2400).tobytes(), "audio/pcm"
    voice, player = tts(base)
    assert voice.say("Buenos días, Carmen.", {"speech_rate": "0.8", "volume": "very_loud"}) is True
    s = Eleven.seen[-1]
    assert s["path"] == "/v1/text-to-speech/VOICE1?output_format=pcm_24000"
    assert s["key"] == KEY and s["ctype"] == "application/json"
    body = json.loads(s["raw"])
    assert body == {"text": "Buenos días, Carmen.", "model_id": "eleven_flash_v2_5", "language_code": "es",
                    "voice_settings": {"speed": 0.8}}
    rate, got = samples(player.played[-1])
    assert rate == 24000 and got[:2] == [10000, -10000] and len(got) == 4800


def test_volume_scales_the_samples(base):
    Eleven.body = array("h", [10000, -10000, 32767, -32768]).tobytes()
    voice, player = tts(base)
    voice.say("hola", {"speech_rate": "1.0", "volume": "soft"})
    assert samples(player.played[-1])[1] == [4500, -4500, 14745, -14746]


def test_speed_stays_in_what_the_api_takes(base):
    Eleven.body = b"\0\0" * 10
    voice, _ = tts(base)
    for rate, speed in (("1.2", 1.2), ("2.0", 1.2), ("0.1", 0.7), ("rápido", 1.0)):
        voice.say("hola", {"speech_rate": rate})
        assert json.loads(Eleven.seen[-1]["raw"])["voice_settings"] == {"speed": speed}
    voice.say("hola", {})
    assert json.loads(Eleven.seen[-1]["raw"])["voice_settings"] == {"speed": 1.0}


def test_multilingual_v2_gets_no_language_code(base):
    Eleven.body = b"\0\0" * 10
    voice, _ = tts(base, model="eleven_multilingual_v2")
    voice.say("hola", {})
    assert "language_code" not in json.loads(Eleven.seen[-1]["raw"])


def test_nothing_to_say_is_said_without_a_call(base):
    voice, player = tts(base)
    assert voice.say("   ", {}) is True and Eleven.seen == [] and player.played == []


@pytest.mark.parametrize("status,error", [(401, "bad_key"), (429, "busy"), (503, "busy"), (400, "rejected")])
def test_say_failures_are_false_and_nothing_is_played(base, status, error):
    Eleven.status, Eleven.body = status, json.dumps({"detail": {"message": "quota"}}).encode()
    voice, player = tts(base)
    assert voice.say("hola", {}) is False and player.played == [] and voice.last_error == error
    assert KEY not in repr(voice)


def test_say_empty_audio_is_false(base):
    Eleven.body = b""
    voice, player = tts(base)
    assert voice.say("hola", {}) is False and player.played == []


def test_say_when_the_speaker_fails_is_false(base):
    Eleven.body = b"\0\0" * 10
    voice, _ = tts(base, player=Player(ok=False))
    assert voice.say("hola", {}) is False


def test_say_when_the_player_raises_is_false(base):
    Eleven.body = b"\0\0" * 10

    def broken(wav):
        raise OSError("no aplay")

    voice, _ = tts(base, player=broken)
    assert voice.say("hola", {}) is False


def test_say_no_connection_is_false_without_the_key():
    voice = ElevenVoice(KEY, voice_id="V", base="http://127.0.0.1:9", timeout=1, player=Player())
    assert voice.say("hola", {}) is False and voice.last_error == "no_connection"
    assert KEY not in repr(voice)


def test_chime_is_a_short_soft_sound_and_never_raises():
    player = Player()
    ElevenVoice(KEY, player=player).chime()
    rate, got = samples(player.played[-1])
    assert 0.1 < len(got) / rate < 1.0 and 0 < max(abs(x) for x in got) < 16000

    def broken(wav):
        raise OSError("no aplay")

    ElevenVoice(KEY, player=broken).chime()


def test_default_player_picks_the_system_player(monkeypatch):
    ran = []

    class R:
        returncode = 0

    def run(cmd, **kw):
        ran.append(cmd)
        return R()

    wav = wav_of(b"\0\0" * 100)
    monkeypatch.setattr(eleven.sys, "platform", "linux")
    assert eleven.play_wav(wav, run=run) is True and ran[-1][:2] == ["aplay", "-q"]
    monkeypatch.setattr(eleven.sys, "platform", "darwin")
    assert eleven.play_wav(wav, run=run) is True and ran[-1][0] == "afplay"

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    assert eleven.play_wav(wav, run=missing) is False


def test_r_kept_lines_are_said_without_the_internet(tmp_path):
    from kore.home.eleven import ElevenVoice
    played = []
    v = ElevenVoice("k", base="http://127.0.0.1:9", timeout=0.5, player=lambda w: played.append(w) or True,
                    cache_dir=tmp_path)
    assert v.prepare(["No he podido avisar a nadie."]) == 0  # offline: nothing to keep yet
    (v._cache_file("No he podido avisar a nadie.")).write_bytes(b"\x00\x01" * 2400)  # as if kept while online
    assert v.say("No he podido avisar a nadie.", {}) and played
    assert not v.say("Otra frase", {})  # not kept, no internet
