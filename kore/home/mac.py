"""Pepe on the Mac, until the box exists: the Mac's microphone and speech recognition as the ears, its built-in
Spanish voice as the mouth. Free, nothing to install or download.

- MacEars: one turn of listening (after the key press, the stand-in for the button), by a small Swift helper built
  on first use (kore/home/swift/ears.swift). Recognition runs on the Mac itself when macOS supports it for Spanish,
  so the audio stays at home.
- MacVoice: macOS `say`, following their speed and volume settings. Blocking: Pepe never listens while talking,
  so he can't hear himself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "swift" / "ears.swift"
VOICE = "Eddy (Spanish (Spain))"
RATES = {"0.8": 140, "0.9": 155, "1.0": 170, "1.1": 185, "1.2": 200}  # words per minute
VOLUMES = {"soft": 0.45, "normal": 0.7, "loud": 0.9, "very_loud": 1.0}
CHIME = "/System/Library/Sounds/Tink.aiff"
# Outside Documents: iCloud labels files there, and macOS then refuses to sign the little app.
BIN_DIR = Path.home() / "Library" / "Application Support" / "KORE"

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.kore.pepe.ears</string>
<key>CFBundleName</key><string>Pepe</string>
<key>CFBundleDisplayName</key><string>Pepe</string>
<key>CFBundleExecutable</key><string>kore-ears</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Pepe escucha solo después de pulsar la tecla.</string>
<key>NSSpeechRecognitionUsageDescription</key><string>Pepe convierte en texto lo que se le dice, en este Mac.</string>
</dict></plist>
"""

ERRORS = {
    "mic_permission": "El Mac no deja a Pepe usar el micrófono. Ajustes del Sistema → Privacidad y seguridad → "
                      "Micrófono: activa «Pepe».",
    "speech_permission": "El Mac no deja a Pepe usar el reconocimiento de voz. Ajustes del Sistema → Privacidad y "
                         "seguridad → Reconocimiento de voz: activa «Pepe».",
    "no_recognizer": "El reconocimiento de voz en español no está disponible ahora en este Mac.",
    "mic_start": "No he podido abrir el micrófono. ¿Está conectado?",
    "build": "No he podido preparar el oído de Pepe (hace falta Xcode o sus herramientas de línea de comandos).",
    "timeout": "El oído de Pepe no ha contestado.",
}


def build(bin_dir: Path) -> Path:
    """A tiny invisible app, "Pepe.app": macOS asks for the microphone and speech recognition only for an app with
    its own Info.plist (a plain command is stopped). Rebuilt when the source changes. Returns the app."""
    app = bin_dir / "Pepe.app"
    exe = app / "Contents" / "MacOS" / "kore-ears"
    if exe.exists() and exe.stat().st_mtime >= SOURCE.stat().st_mtime:
        return app
    exe.parent.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(PLIST)
    subprocess.run(["swiftc", "-O", "-swift-version", "5", str(SOURCE), "-o", str(exe)], check=True,
                   capture_output=True, timeout=300)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True, timeout=60)
    return app


@dataclass
class Heard:
    text: str
    ended_at: float | None = None   # monotonic time they stopped speaking, for "end of speech to first word"
    error: str | None = None        # a key of ERRORS
    on_device: bool = True


class MacEars:
    def __init__(self, bin_dir: Path | None = None, locale: str = "es-ES", on_ready=None):
        self.bin_dir = bin_dir or BIN_DIR
        self.locale = locale
        self.on_ready = on_ready  # called once it is really listening ("Le escucho")

    def listen(self) -> Heard:
        try:
            app = build(self.bin_dir)
        except (OSError, subprocess.SubprocessError):
            return Heard("", error="build")
        out = self.bin_dir / "heard.jsonl"
        out.write_text("")
        try:  # through LaunchServices, so the app (not the terminal) is the one macOS asks about
            proc = subprocess.Popen(["open", "-W", "-n", "-g", "--stdout", str(out), "--stderr", "/dev/null",
                                     str(app), "--args", self.locale],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return Heard("", error="build")
        result: dict = {}
        ready = False
        deadline = time.monotonic() + 90  # the first time, the permission questions wait for a click
        while time.monotonic() < deadline:
            for line in out.read_text().splitlines():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("ready") and not ready:
                    ready = True
                    if self.on_ready:
                        self.on_ready()
                elif not msg.get("ready"):
                    result = msg
            if result or proc.poll() is not None and not result and _settled(out):
                break
            time.sleep(0.05)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not result:
            for line in out.read_text().splitlines():  # a last line written as it closed
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not msg.get("ready"):
                    result = msg
        if not result:
            return Heard("", error="timeout")
        if result.get("error"):
            return Heard("", error=result["error"])
        silence = (result.get("silence_ms") or 0) / 1000
        return Heard((result.get("text") or "").strip(), time.monotonic() - silence, None,
                     bool(result.get("on_device")))


def _settled(path: Path) -> bool:
    time.sleep(0.2)
    return True


class MacVoice:
    """The Voice interface (kore/home/voice.py) with the Mac's own voice."""

    def __init__(self, voice: str = VOICE, run=subprocess.run):
        self.voice = voice
        self._run = run

    def say(self, text: str, settings: dict) -> bool:
        rate = RATES.get(str(settings.get("speech_rate", "1.0")), 170)
        volume = VOLUMES.get(settings.get("volume", "normal"), 0.7)
        try:
            r = self._run(["say", "-v", self.voice, "-r", str(rate), f"[[volm {volume}]] {text}"],
                          capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def chime(self) -> None:
        try:
            self._run(["afplay", CHIME], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def available() -> bool:
    return sys.platform == "darwin"
