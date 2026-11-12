"""ElevenLabs as Pepe's ears and mouth on the box (phase 1): one interface each, swappable.

- ElevenSTT: speech to text with Scribe. `transcribe(wav) -> str | None` (None: it failed; never raises).
- ElevenVoice: the Voice interface (kore/home/voice.py). Text to speech, then played through a player
  (default: `aplay` on the Raspberry Pi, `afplay` on a Mac). Blocking: Pepe never listens while talking.

Switched off until Lea sets ELEVENLABS_API_KEY (from_env returns (None, None) without it). Optional:
ELEVENLABS_VOICE_ID (default DEFAULT_VOICE_ID below) and ELEVENLABS_TTS_MODEL (default TTS_MODEL).

Checked against the official API reference on 2026-11-08:
- https://elevenlabs.io/docs/api-reference/speech-to-text/convert
    POST /v1/speech-to-text, header `xi-api-key`, multipart/form-data: `model_id` (required), `file`,
    `language_code` (ISO-639-1 or -3), `tag_audio_events`, `file_format` ("pcm_s16le_16" = 16-bit 16 kHz mono
    little-endian PCM, lower latency, or "other"). Answer: JSON with `text`, `language_code`, `words`.
- https://elevenlabs.io/docs/api-reference/text-to-speech/convert
    POST /v1/text-to-speech/{voice_id}?output_format=..., header `xi-api-key`, JSON body: `text`, `model_id`,
    `language_code` ("not supported for multilingual_v2 models"; ignored by models that don't take it),
    `voice_settings.speed` (default 1.0). Answer: the audio file. output_format "pcm_24000" is raw PCM
    (16-bit little-endian mono) at 24 kHz; 44.1 kHz needs a Pro plan, so it isn't used.
- https://elevenlabs.io/docs/overview/models
    eleven_flash_v2_5: Spanish, ~100 ms; eleven_multilingual_v2: Spanish, slower. scribe_v2 (scribe_v1 deprecated).
- https://elevenlabs.io/docs/eleven-agents/customization/voice/speed-control  (speed 0.7 to 1.2)

DEFAULT_VOICE_ID is "George", the premade voice in ElevenLabs' own API examples: it speaks Spanish through the
multilingual models but with a foreign accent. For Pepe, Lea picks a Spanish (Spain) voice in the Voice Library
and puts its id in ELEVENLABS_VOICE_ID.

Plain standard library (urllib, wave, array): no new dependency. The key is never logged, printed or put in an
error or a repr; errors are kept as a short word in `last_error`.
"""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from array import array
from pathlib import Path

API_KEY_VAR = "ELEVENLABS_API_KEY"
VOICE_ID_VAR = "ELEVENLABS_VOICE_ID"
TTS_MODEL_VAR = "ELEVENLABS_TTS_MODEL"

BASE = "https://api.elevenlabs.io"
STT_MODEL = "scribe_v2"
TTS_MODEL = "eleven_flash_v2_5"          # Spanish, the fastest; "eleven_multilingual_v2" sounds a little richer
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" (premade). Lea sets a Spanish one: see the docstring
OUTPUT_FORMAT = "pcm_24000"
OUTPUT_RATE = 24000
TIMEOUT_S = 15

SPEED_MIN, SPEED_MAX = 0.7, 1.2
VOLUMES = {"soft": 0.45, "normal": 0.7, "loud": 0.9, "very_loud": 1.0}  # as on the Mac (kore/home/mac.py)
MIN_STT_S = 0.1                          # Scribe needs at least 100 ms


# --- HTTP -------------------------------------------------------------------------------------------------------


def _request(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """(HTTP status, body). 0 means no connection (or a malformed key or URL). Never raises, never echoes the URL
    or the headers."""
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception:  # no connection, a timeout, a key with a stray character: no details, ever
        return 0, b""


def _error(status: int) -> str:
    if status == 0:
        return "no_connection"
    if status in (401, 403):
        return "bad_key"        # wrong key, or the plan's quota is used up
    if status == 429 or status >= 500:
        return "busy"           # try again later
    return "rejected"           # 400, 422: something in the request it didn't take


# --- speech to text ---------------------------------------------------------------------------------------------


class ElevenSTT:
    def __init__(self, api_key: str, base: str = BASE, model: str = STT_MODEL, timeout: float = TIMEOUT_S):
        self._key = api_key
        self._base = base.rstrip("/")
        self.model = model
        self._timeout = timeout
        self.last_error: str | None = None  # no_connection | bad_key | busy | rejected

    def __repr__(self) -> str:  # never show the key
        return f"ElevenSTT(model={self.model!r})"

    def transcribe(self, wav_bytes: bytes, language: str = "es") -> str | None:
        """The words in the recording ("" if none), or None if Scribe couldn't be asked or didn't answer."""
        self.last_error = None
        try:
            audio, file_format, seconds = _for_scribe(wav_bytes)
        except Exception:
            self.last_error = "rejected"
            return None
        if seconds is not None and seconds < MIN_STT_S:
            return ""
        fields = [("model_id", self.model), ("language_code", language), ("tag_audio_events", "false")]
        if file_format:
            fields.append(("file_format", file_format))
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields]
        name, ctype = ("audio.pcm", "application/octet-stream") if file_format else ("audio.wav", "audio/wav")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + audio + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, body = _request(f"{self._base}/v1/speech-to-text", b"".join(parts),
                                {"xi-api-key": self._key,
                                 "Content-Type": f"multipart/form-data; boundary={boundary}"}, self._timeout)
        if status != 200:
            self.last_error = _error(status)
            return None
        try:
            text = json.loads(body.decode("utf-8")).get("text")
        except (ValueError, UnicodeDecodeError, AttributeError):
            text = None
        if not isinstance(text, str):
            self.last_error = "rejected"
            return None
        return text.strip()


def _for_scribe(wav_bytes: bytes) -> tuple[bytes, str | None, float | None]:
    """(audio, file_format, seconds). A 16 kHz mono 16-bit WAV goes as raw PCM (faster for Scribe); anything
    else goes as it is."""
    if wav_bytes[:4] != b"RIFF":
        raise ValueError("not a WAV")
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        seconds = n / rate if rate else None
        if (rate, channels, width) == (16000, 1, 2):
            return w.readframes(n), "pcm_s16le_16", seconds
    return wav_bytes, None, seconds


# --- text to speech ---------------------------------------------------------------------------------------------


def play_wav(wav: bytes, run=subprocess.run) -> bool:
    """Plays a WAV through the system's player and waits for the end. False if it couldn't."""
    cmd = ["afplay"] if sys.platform == "darwin" else ["aplay", "-q"]
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="kore-", suffix=".wav", delete=False) as f:
            f.write(wav)
            path = f.name
        seconds = max(0, len(wav) - 44) / (2 * 16000)  # generous: the lowest rate it could be
        r = run(cmd + [path], capture_output=True, timeout=seconds + 15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if path:
            Path(path).unlink(missing_ok=True)  # our own temporary sound file, not one of Lea's


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _scaled(pcm: bytes, gain: float) -> bytes:
    """16-bit little-endian samples times gain, kept in range."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if gain != 1.0:
        samples = array("h", (max(-32768, min(32767, round(s * gain))) for s in samples))
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _speed(settings: dict) -> float:
    try:
        speed = float(settings.get("speech_rate", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(speed):
        return 1.0
    return round(min(SPEED_MAX, max(SPEED_MIN, speed)), 2)


class ElevenVoice:
    """The Voice interface with an ElevenLabs voice. `say` returns False when it couldn't be said (no connection,
    bad key, quota, the speaker failed); the loop says it later or falls back. It can't yet tell that someone
    talked over it: that needs the speakerphone's echo cancelling (phase 1, on the box)."""

    def __init__(self, api_key: str, voice_id: str = DEFAULT_VOICE_ID, model: str = TTS_MODEL, base: str = BASE,
                 timeout: float = TIMEOUT_S, player=play_wav, language: str = "es", cache_dir: Path | None = None):
        self._key = api_key
        # Lines kept on the box (prepare): said from here, with no internet and at no cost. Only code's fixed
        # lines (help outcomes, "I can't hear you"...), so Pepe is never silent when the connection is down.
        self.cache_dir = cache_dir
        self.voice_id = voice_id
        self.model = model
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._player = player
        self.language = language
        self.last_error: str | None = None  # no_connection | bad_key | busy | rejected | no_audio | speaker

    def __repr__(self) -> str:  # never show the key
        return f"ElevenVoice(voice_id={self.voice_id!r}, model={self.model!r})"

    def say(self, text: str, settings: dict) -> bool:
        self.last_error = None
        if not text or not text.strip():
            return True
        settings = settings or {}
        gain = VOLUMES.get(settings.get("volume", "normal"), VOLUMES["normal"])
        kept = self._cached(text)
        if kept is not None:
            return self._play(_wav(_scaled(kept, gain), OUTPUT_RATE))
        pcm = self._synth(text, settings)
        if pcm is None:
            return False
        return self._play(_wav(_scaled(pcm, gain), OUTPUT_RATE))

    def prepare(self, lines: list[str], settings: dict | None = None) -> int:
        """Record these lines once, while online, to say them later without the internet. Returns how many are
        kept now. Never raises."""
        if self.cache_dir is None:
            return 0
        kept = 0
        for line in lines:
            if self._cached(line) is not None:
                kept += 1
                continue
            pcm = self._synth(line, settings or {})
            if pcm is None:
                continue
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = self._cache_file(line).with_suffix(".tmp")
                tmp.write_bytes(pcm)
                tmp.replace(self._cache_file(line))
                kept += 1
            except OSError:
                pass
        return kept

    def _cache_file(self, text: str) -> Path:
        import hashlib
        name = hashlib.sha256(f"{self.voice_id}|{self.model}|{text}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{name}.pcm"

    def _cached(self, text: str) -> bytes | None:
        if self.cache_dir is None:
            return None
        try:
            return self._cache_file(text).read_bytes()
        except OSError:
            return None

    def _synth(self, text: str, settings: dict) -> bytes | None:
        body = {"text": text, "model_id": self.model}
        if not self.model.startswith("eleven_multilingual_v2"):
            body["language_code"] = self.language
        body["voice_settings"] = {"speed": _speed(settings)}
        url = (f"{self._base}/v1/text-to-speech/{urllib.parse.quote(self.voice_id, safe='')}"
               f"?output_format={OUTPUT_FORMAT}")
        status, pcm = _request(url, json.dumps(body).encode(), {"xi-api-key": self._key,
                                                                 "Content-Type": "application/json"},
                               self._timeout)
        if status != 200:
            self.last_error = _error(status)
            return None
        if len(pcm) < 2:
            self.last_error = "no_audio"
            return None
        return pcm

    def chime(self) -> None:
        """Two soft notes, made here: no sound file to ship."""
        rate, notes = 24000, (1318.5, 1046.5)  # E6, C6
        out = array("h")
        for hz in notes:
            n = int(rate * 0.16)
            for i in range(n):
                fade = min(1.0, i / 240, (n - i) / 2400)
                out.append(int(6000 * fade * math.sin(2 * math.pi * hz * i / rate)))
        if sys.byteorder == "big":
            out.byteswap()
        self._play(_wav(out.tobytes(), rate))

    def _play(self, wav: bytes) -> bool:
        try:
            ok = bool(self._player(wav))
        except Exception:
            ok = False
        if not ok:
            self.last_error = "speaker"
        return ok


def from_env(env=None, cache_dir: Path | None = None) -> tuple[ElevenSTT | None, ElevenVoice | None]:
    """(ears, voice), or (None, None) until Lea sets ELEVENLABS_API_KEY."""
    env = os.environ if env is None else env
    key = (env.get(API_KEY_VAR) or "").strip()
    if not key:
        return None, None
    voice_id = (env.get(VOICE_ID_VAR) or "").strip() or DEFAULT_VOICE_ID
    model = (env.get(TTS_MODEL_VAR) or "").strip() or TTS_MODEL
    return ElevenSTT(key), ElevenVoice(key, voice_id=voice_id, model=model, cache_dir=cache_dir)
