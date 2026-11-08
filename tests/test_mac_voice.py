"""Pepe on the Mac: the voice (macOS `say`) and the ears (a small app), without the real microphone."""

import json
import subprocess

from kore.home import mac


class Run:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, args, **kw):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, self.code)


def test_the_voice_follows_their_speed_and_volume():
    run = Run()
    v = mac.MacVoice(run=run)
    assert v.say("Hola, Carmen.", {"speech_rate": "0.8", "volume": "loud"})
    args = run.calls[0]
    assert args[:3] == ["say", "-v", mac.VOICE] and args[3:5] == ["-r", "140"]
    assert args[5] == "[[volm 0.9]] Hola, Carmen."


def test_a_voice_that_fails_says_so():
    assert not mac.MacVoice(run=Run(1)).say("Hola", {})

    def broken(*a, **k):
        raise OSError("no say")
    assert not mac.MacVoice(run=broken).say("Hola", {})


class FakeOpen:
    """Stands in for `open … Pepe.app`: writes what the app would print to its --stdout file."""

    def __init__(self, lines):
        self.lines = lines

    def __call__(self, args, **kw):
        out = args[args.index("--stdout") + 1]
        with open(out, "w") as f:
            for line in self.lines:
                f.write(json.dumps(line) + "\n")
        return self

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def ears(monkeypatch, tmp_path, lines):
    monkeypatch.setattr(mac, "build", lambda d: tmp_path / "Pepe.app")
    monkeypatch.setattr(mac.subprocess, "Popen", FakeOpen(lines))
    ready = []
    e = mac.MacEars(bin_dir=tmp_path, on_ready=lambda: ready.append(1))
    return e.listen(), ready


def test_the_ears_return_what_was_said(monkeypatch, tmp_path):
    heard, ready = ears(monkeypatch, tmp_path, [{"ready": True},
                                                {"text": " ¿Qué hora es? ", "on_device": True, "silence_ms": 1300}])
    assert heard.text == "¿Qué hora es?" and heard.error is None and heard.on_device and ready == [1]
    assert heard.ended_at is not None


def test_a_missing_permission_is_explained(monkeypatch, tmp_path):
    heard, _ = ears(monkeypatch, tmp_path, [{"error": "mic_permission"}])
    assert heard.error == "mic_permission" and "Micrófono" in mac.ERRORS[heard.error]


def test_nothing_from_the_app_is_a_timeout(monkeypatch, tmp_path):
    heard, _ = ears(monkeypatch, tmp_path, [])
    assert heard.error == "timeout"


def test_the_ears_are_built_outside_icloud_documents():
    assert "Documents" not in str(mac.BIN_DIR)
